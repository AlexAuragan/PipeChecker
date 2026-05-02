from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from starlette.datastructures import FormData

from src.api.web_auth import require_web_auth
from src.api.website.utils import _fget, templates
from src.classes.alert_connector import AlertConnector, DiscordConnector, RSSConnector, WebhookConnector
from src.classes.enums import AlertConnectorType
from src.core import storage

router = APIRouter(tags=["alert_connector"], dependencies=[Depends(require_web_auth)])

_ALL_TYPES = list(AlertConnectorType)


def _parse_alert_connector_form(form: FormData) -> AlertConnector:
    name = _fget(form, "name").strip()
    ctype = AlertConnectorType(_fget(form, "type"))

    if ctype == AlertConnectorType.rss:
        data: dict[str, Any] = {
            "name": name,
            "feed_path": _fget(form, "feed_path").strip(),
            "max_items": _fget(form, "max_items", "50").strip() or "50",
        }
        if title := _fget(form, "title_template").strip():
            data["title_template"] = title
        if desc := _fget(form, "description_template").strip():
            data["description_template"] = desc
        return RSSConnector.model_validate(data)

    header_keys = [v for k, v in form.multi_items() if k == "header_key" and isinstance(v, str)]
    header_vals = [v for k, v in form.multi_items() if k == "header_value" and isinstance(v, str)]
    headers = {k: v for k, v in zip(header_keys, header_vals) if k.strip()}
    data = {"name": name, "url": _fget(form, "url").strip(), "headers": headers}
    if body := _fget(form, "body_template").strip():
        data["body_template"] = body
    if ctype == AlertConnectorType.discord:
        return DiscordConnector.model_validate(data)
    return WebhookConnector.model_validate(data)


def _form_data_from_connector(c: AlertConnector) -> dict[str, Any]:
    d = c.model_dump(mode="json")
    return {
        "name": d["name"],
        "type": d["type"],
        "feed_path": str(d.get("feed_path", "")),
        "title_template": d.get("title_template", ""),
        "description_template": d.get("description_template", ""),
        "max_items": str(d.get("max_items", 50)),
        "url": d.get("url", ""),
        "body_template": d.get("body_template", ""),
        "headers": d.get("headers", {}),
    }


def _form_data_from_form(form: FormData, name_override: str | None = None) -> dict[str, Any]:
    header_keys = [v for k, v in form.multi_items() if k == "header_key" and isinstance(v, str)]
    header_vals = [v for k, v in form.multi_items() if k == "header_value" and isinstance(v, str)]
    return {
        "name": name_override or _fget(form, "name"),
        "type": _fget(form, "type", AlertConnectorType.webhook.value),
        "feed_path": _fget(form, "feed_path"),
        "title_template": _fget(form, "title_template"),
        "description_template": _fget(form, "description_template"),
        "max_items": _fget(form, "max_items", "50"),
        "url": _fget(form, "url"),
        "body_template": _fget(form, "body_template"),
        "headers": dict(zip(header_keys, header_vals)),
    }


def _validation_errors(exc: ValidationError | ValueError | KeyError) -> list[str]:
    if isinstance(exc, ValidationError):
        return [f"{' → '.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return [str(exc)]


def _form_ctx(form_data: dict[str, Any], editing: bool = False, errors: list[str] | None = None) -> dict[str, Any]:
    return {"editing": editing, "form_data": form_data, "errors": errors, "all_types": _ALL_TYPES}


@router.get("", response_class=HTMLResponse)
def alert_connectors_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="alert_connectors.html",
        context={"request": request, "connectors": storage.load_alert_connectors()},
    )


@router.get("/new", response_class=HTMLResponse)
def new_alert_connector_page(request: Request) -> HTMLResponse:
    form_data = {
        "name": "",
        "type": AlertConnectorType.webhook.value,
        "feed_path": "",
        "title_template": "",
        "description_template": "",
        "max_items": "50",
        "url": "",
        "body_template": "",
        "headers": {},
    }
    return templates.TemplateResponse(
        request=request,
        name="alert_connector_form.html",
        context={"request": request, **_form_ctx(form_data)},
    )


@router.post("/new", response_class=HTMLResponse)
async def create_alert_connector_route(request: Request) -> Response:
    form = await request.form()
    try:
        connector = _parse_alert_connector_form(form)
        storage.save_alert_connector(connector)
    except (ValidationError, ValueError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="alert_connector_form.html",
            status_code=422,
            context={"request": request, **_form_ctx(_form_data_from_form(form), errors=_validation_errors(exc))},
        )
    return RedirectResponse("/alert-connector", status_code=303)


@router.get("/{name}/edit", response_class=HTMLResponse)
def edit_alert_connector_page(request: Request, name: str) -> Response:
    by_name = {c.name: c for c in storage.load_alert_connectors()}
    if name not in by_name:
        return RedirectResponse("/alert-connector", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="alert_connector_form.html",
        context={"request": request, **_form_ctx(_form_data_from_connector(by_name[name]), editing=True)},
    )


@router.post("/{name}/edit", response_class=HTMLResponse)
async def update_alert_connector_route(request: Request, name: str) -> Response:
    form = await request.form()
    try:
        connector = _parse_alert_connector_form(form)
        storage.update_alert_connector(connector)
    except (ValidationError, ValueError, KeyError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="alert_connector_form.html",
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
    return RedirectResponse("/alert-connector", status_code=303)


@router.post("/{name}/delete", response_class=HTMLResponse)
async def delete_alert_connector_route(request: Request, name: str) -> RedirectResponse:
    try:
        storage.delete_alert_connector(name)
    except KeyError:
        pass
    return RedirectResponse("/alert-connector", status_code=303)
