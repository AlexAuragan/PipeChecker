from fastapi import Request, APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from src.api import utils
from src.api.website.utils import (
    available_connectors, parse_pipeline_form, steps_from_form,
    form_base_ctx, compute_columns, build_edges, templates,
)
from src.core import jobs, storage as _storage

router = APIRouter(tags=["pipeline"])


def _validation_errors(exc: ValidationError | ValueError) -> list[str]:
    """Format a Pydantic or plain ValueError into a list of human-readable strings."""
    if hasattr(exc, "errors"):
        return [f"{' → '.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return [str(exc)]


@router.get("/new", response_class=HTMLResponse)
def new_pipeline_page(request: Request):
    return templates.TemplateResponse(request=request, name="pipeline_form.html", context={
        **form_base_ctx(),
        "request": request,
        "editing": False,
        "form_data": {
            "name": "", "group": "default", "cron": "0 * * * *",
            "runner": "", "connectors": [],
        },
        "steps": [],
        "all_step_ids": [],
        "available_connectors": available_connectors(),
        "errors": None,
    })


@router.post("/new", response_class=HTMLResponse)
async def create_pipeline(request: Request):
    form = await request.form()
    try:
        group, pipeline = parse_pipeline_form(form)
    except (ValidationError, ValueError) as exc:
        errors = _validation_errors(exc)
        steps = steps_from_form(form)
        return templates.TemplateResponse(request=request, name="pipeline_form.html", status_code=422, context={
            **form_base_ctx(),
            "request": request,
            "editing": False,
            "form_data": {
                "name": form.get("name", ""), "group": form.get("group", "default"),
                "cron": form.get("cron", ""), "runner": form.get("runner", ""),
                "connectors": [v for k, v in form.multi_items() if k == "connectors"],
            },
            "steps": steps,
            "all_step_ids": [s["id"] for _, s in steps if s["id"]],
            "available_connectors": available_connectors(),
            "errors": errors,
        })
    _storage.save_pipeline(pipeline, group)
    return RedirectResponse(f"/pipeline/{pipeline.name}", status_code=303)


@router.get("/{name}/edit", response_class=HTMLResponse)
def edit_pipeline_page(request: Request, name: str):
    pipeline, group = utils.get_pipeline_or_404(name, None)
    steps = [(i, {
        "id":             step.id,
        "exec":           step.exec,
        "exec_method":    step.exec_method.value,
        "exec_command":   step.exec if step.exec_method.value == "command" else "",
        "exec_script":    step.exec if step.exec_method.value == "script" else "",
        "check_method":   step.check_method.value,
        "check_patterns": step.check_patterns or [],
        "branches":       [{"name": b.name, "signal": b.signal.value} for b in step.branches],
        "requires":       [{"step": r.step, "branch": r.branch} for r in step.requires],
    }) for i, step in enumerate(pipeline.pipeline)]
    return templates.TemplateResponse(request=request, name="pipeline_form.html", context={
        **form_base_ctx(),
        "request": request,
        "editing": True,
        "form_data": {
            "name": pipeline.name, "group": group,
            "cron": pipeline.cron, "runner": pipeline.runner.value,
            "connectors": pipeline.connectors,
        },
        "steps": steps,
        "all_step_ids": [s.id for s in pipeline.pipeline],
        "available_connectors": available_connectors(),
        "errors": None,
    })


@router.post("/{name}/edit", response_class=HTMLResponse)
async def update_pipeline_route(request: Request, name: str):
    form = await request.form()
    try:
        group, pipeline = parse_pipeline_form(form)
    except (ValidationError, ValueError) as exc:
        errors = _validation_errors(exc)
        steps = steps_from_form(form)
        return templates.TemplateResponse(request=request, name="pipeline_form.html", status_code=422, context={
            **form_base_ctx(),
            "request": request,
            "editing": True,
            "form_data": {
                "name": name, "group": form.get("group", "default"),
                "cron": form.get("cron", ""), "runner": form.get("runner", ""),
                "connectors": [v for k, v in form.multi_items() if k == "connectors"],
            },
            "steps": steps,
            "all_step_ids": [s["id"] for _, s in steps if s["id"]],
            "available_connectors": available_connectors(),
            "errors": errors,
        })
    _storage.update_pipeline(pipeline, group)
    return RedirectResponse(f"/pipeline/{pipeline.name}", status_code=303)


@router.get("/{name}", response_class=HTMLResponse)
def pipeline_page(request: Request, name: str):
    pipeline, group = utils.get_pipeline_or_404(name, None)
    columns = compute_columns(pipeline.pipeline)
    edges = build_edges(pipeline.pipeline)
    pipeline_jobs = [j for j in jobs.list_jobs() if j["pipeline_name"] == name]

    return templates.TemplateResponse(request=request, name="pipeline.html", context={
        "request": request,
        "pipeline": pipeline,
        "group": group,
        "columns": columns,
        "edges": edges,
        "pipeline_jobs": pipeline_jobs[:20],
    })


@router.post("/{name}/delete", response_class=HTMLResponse)
async def delete_pipeline_route(request: Request, name: str):
    pipeline, group = utils.get_pipeline_or_404(name, None)
    _storage.delete_pipeline(name, group)
    return RedirectResponse("/", status_code=303)
