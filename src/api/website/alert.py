from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from src.api.web_auth import require_web_auth
from src.api.website.utils import templates
from src.classes.alert import AlertConfig
from src.classes.enums import Status
from src.core import jobs, storage

router = APIRouter(tags=["alert"], dependencies=[Depends(require_web_auth)])

_ALL_SIGNALS = list(Status)


def _parse_alert_form(form) -> AlertConfig:
    name = (form.get("name") or "").strip()
    pipeline = (form.get("pipeline") or "").strip() or None
    on_signals_raw = [v for k, v in form.multi_items() if k == "on_signals"]
    connector = (form.get("connector") or "").strip() or None
    return AlertConfig.model_validate(
        {
            "name": name,
            "pipeline": pipeline,
            "on_signals": on_signals_raw,
            "connector": connector,
        }
    )


def _form_data_from_form(form, name_override: str | None = None) -> dict[str, Any]:
    on_signals = []
    for v in (v for k, v in form.multi_items() if k == "on_signals"):
        try:
            on_signals.append(Status(v))
        except ValueError:
            pass
    return {
        "name": name_override or (form.get("name") or "").strip(),
        "pipeline": (form.get("pipeline") or "").strip() or None,
        "on_signals": on_signals,
        "connector": (form.get("connector") or "").strip() or None,
    }


def _validation_errors(exc: ValidationError | ValueError | KeyError) -> list[str]:
    if isinstance(exc, ValidationError):
        return [
            f"{' → '.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()
        ]
    return [str(exc)]


def _form_ctx(
    form_data: dict[str, Any], editing: bool = False, errors: list[str] | None = None
) -> dict[str, Any]:
    return {
        "editing": editing,
        "form_data": form_data,
        "errors": errors,
        "all_signals": _ALL_SIGNALS,
        "available_pipelines": _available_pipeline_names(),
        "available_alert_connectors": storage.load_alert_connectors(),
    }


def _available_pipeline_names() -> list[str]:
    return sorted(name for pipes in storage.load_pipelines().values() for name in pipes)


@router.get("", response_class=HTMLResponse)
def alerts_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="alerts.html",
        context={
            "request": request,
            "alerts": storage.load_alerts(),
            "history": jobs.list_alert_history(limit=100),
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_alert_page(request: Request, pipeline: str = ""):
    form_data = {
        "name": "",
        "pipeline": pipeline or None,
        "on_signals": [Status.fail, Status.crashed],
        "connector": None,
    }
    return templates.TemplateResponse(
        request=request,
        name="alert_form.html",
        context={
            "request": request,
            **_form_ctx(form_data),
        },
    )


@router.post("/new", response_class=HTMLResponse)
async def create_alert_route(request: Request):
    form = await request.form()
    try:
        storage.save_alert(_parse_alert_form(form))
    except (ValidationError, ValueError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="alert_form.html",
            status_code=422,
            context={
                "request": request,
                **_form_ctx(_form_data_from_form(form), errors=_validation_errors(exc)),
            },
        )
    return RedirectResponse("/alert", status_code=303)


@router.post("/history/clear", response_class=HTMLResponse)
async def clear_history_route(request: Request):
    jobs.clear_alert_history()
    return RedirectResponse("/alert", status_code=303)


@router.get("/{name}/edit", response_class=HTMLResponse)
def edit_alert_page(request: Request, name: str):
    alerts = {a.name: a for a in storage.load_alerts()}
    if name not in alerts:
        return RedirectResponse("/alert", status_code=303)
    a = alerts[name]
    form_data = {
        "name": a.name,
        "pipeline": a.pipeline,
        "on_signals": a.on_signals,
        "connector": a.connector,
    }
    return templates.TemplateResponse(
        request=request,
        name="alert_form.html",
        context={
            "request": request,
            **_form_ctx(form_data, editing=True),
        },
    )


@router.post("/{name}/edit", response_class=HTMLResponse)
async def update_alert_route(request: Request, name: str):
    form = await request.form()
    try:
        alert = _parse_alert_form(form)
        # Name is readonly in edit mode — enforce it matches the URL
        if alert.name != name:
            alert = AlertConfig(
                name=name,
                pipeline=alert.pipeline,
                on_signals=alert.on_signals,
                connector=alert.connector,
            )
        storage.update_alert(alert)
    except (ValidationError, ValueError, KeyError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="alert_form.html",
            status_code=422,
            context={
                "request": request,
                **_form_ctx(
                    _form_data_from_form(form, name_override=name),
                    editing=True,
                    errors=_validation_errors(exc),
                ),
            },
        )
    return RedirectResponse("/alert", status_code=303)


@router.post("/{name}/delete", response_class=HTMLResponse)
async def delete_alert_route(request: Request, name: str):
    try:
        storage.delete_alert(name)
    except KeyError:
        pass
    return RedirectResponse("/alert", status_code=303)
