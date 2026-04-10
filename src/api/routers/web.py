import json
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from src.api import utils
from src.classes import connectors as _connectors_module
from src.classes.connectors import ConnectorType, Connector
from src.classes.pipeline import Pipeline, PipelineStep, CheckMethod
from src.classes.runner import RunnerType
from src.core import jobs, storage as _storage

router = APIRouter(tags=["web"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_FORM_BASE = {"runner_types": list(RunnerType), "check_methods": list(CheckMethod)}


# ── helpers ───────────────────────────────────────────────────────────────────

def _compute_columns(steps):
    """Assign each step to a column by longest-path depth in the dependency graph."""
    step_map = {s.id: s for s in steps}
    depths: dict[str, int] = {}

    def depth(sid: str) -> int:
        if sid in depths:
            return depths[sid]
        step = step_map[sid]
        depths[sid] = 0 if not step.requires else max(depth(r) for r in step.requires) + 1
        return depths[sid]

    for s in steps:
        depth(s.id)

    num_cols = max(depths.values()) + 1 if depths else 1
    columns: list[list] = [[] for _ in range(num_cols)]
    for s in steps:
        columns[depths[s.id]].append(s)
    return columns


def _build_edges(steps) -> str:
    edges = [{"from": req, "to": step.id} for step in steps for req in step.requires]
    return json.dumps(edges)


def _status_badge(status) -> str:
    """Map a job/target status value to a CSS badge class."""
    s = status.value if hasattr(status, "value") else str(status)
    return {
        "completed": "badge-green",
        "green":     "badge-green",
        "failed":    "badge-red",
        "red":       "badge-red",
        "crashed":   "badge-crashed",
        "orange":    "badge-orange",
        "running":   "badge-blue",
        "pending":   "badge-gray",
        "cancelled": "badge-gray",
    }.get(s, "badge-gray")


def _step_class(step_result: dict) -> str:
    if step_result["skipped"]:   return "status-skipped"
    if step_result["tried_fix"]: return "status-orange"
    if step_result["success"]:   return "status-green"
    return "status-red"


def _step_badge(step_result: dict) -> str:
    if step_result["skipped"]:   return "badge-gray"
    if step_result["tried_fix"]: return "badge-orange"
    if step_result["success"]:   return "badge-green"
    return "badge-red"


def _step_text(step_result: dict) -> str:
    if step_result["skipped"]:   return "skipped"
    if step_result["tried_fix"]: return "fixed"
    if step_result["success"]:   return "pass"
    return "fail"


def _source_badge(source) -> str:
    s = source.value if hasattr(source, "value") else str(source)
    return {"manual": "badge-gray", "cron": "badge-blue", "event": "badge-orange"}.get(s, "badge-gray")


def _fmt_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


templates.env.globals.update(
    status_badge=_status_badge,
    source_badge=_source_badge,
    step_class=_step_class,
    step_badge=_step_badge,
    step_text=_step_text,
    fmt_duration=_fmt_duration,
)


# ── form helpers ──────────────────────────────────────────────────────────────

def _available_connectors() -> list[str]:
    return [c.name for c in _storage.load_manager()]


def _steps_from_form(form) -> list[tuple[int, dict]]:
    """Re-inflate step rows from raw POST form data (for error re-render)."""
    indices = sorted({
        int(k[len("step_id_"):])
        for k in form.keys()
        if k.startswith("step_id_")
    })
    return [(i, {
        "id":           form.get(f"step_id_{i}", ""),
        "exec":         form.get(f"step_exec_{i}", ""),
        "check_method": form.get(f"step_check_method_{i}", "exit_code"),
        "check_pattern":form.get(f"step_check_pattern_{i}", "") or "",
        "if_failed":    form.get(f"step_if_failed_{i}", "") or "",
        "requires":     [v for k, v in form.multi_items() if k == f"step_requires_{i}"],
    }) for i in indices]


def _step_ids_from_form(form) -> list[str]:
    indices = sorted({
        int(k[len("step_id_"):])
        for k in form.keys()
        if k.startswith("step_id_")
    })
    return [sid for i in indices if (sid := (form.get(f"step_id_{i}") or "").strip())]


def _parse_pipeline_form(form) -> tuple[str, Pipeline]:
    group = (form.get("group") or "default").strip() or "default"
    name  = (form.get("name")  or "").strip()
    cron  = (form.get("cron")  or "").strip()
    runner_val      = (form.get("runner") or "").strip()
    connector_list  = [v for k, v in form.multi_items() if k == "connectors"]

    indices = sorted({
        int(k[len("step_id_"):])
        for k in form.keys()
        if k.startswith("step_id_")
    })
    steps = []
    for i in indices:
        sid = (form.get(f"step_id_{i}") or "").strip()
        if not sid:
            continue
        steps.append({
            "id":            sid,
            "exec":          (form.get(f"step_exec_{i}") or "").strip(),
            "check_method":  form.get(f"step_check_method_{i}") or "exit_code",
            "check_pattern": (form.get(f"step_check_pattern_{i}") or "").strip() or None,
            "if_failed":     (form.get(f"step_if_failed_{i}") or "").strip() or None,
            "requires":      [v for k, v in form.multi_items() if k == f"step_requires_{i}"],
        })
    return group, Pipeline.model_validate({
        "name": name, "cron": cron, "runner": runner_val,
        "connectors": connector_list, "pipeline": steps,
    })


def _parse_connector_form(form) -> Connector:
    name      = (form.get("name") or "").strip()
    type_val  = (form.get("type") or "").strip()
    config_ssh  = [v.strip() for k, v in form.multi_items() if k == "config_ssh"  and v.strip()]
    config_url  = [v.strip() for k, v in form.multi_items() if k == "config_url"  and v.strip()]
    config_path = [v.strip() for k, v in form.multi_items() if k == "config_path" and v.strip()]
    connector_type = ConnectorType(type_val)
    cls = _connectors_module[connector_type.value]
    return cls.model_validate({
        "name": name, "config_ssh": config_ssh,
        "config_url": config_url, "config_path": config_path,
    })


def _connector_form_data(form, name_override: str | None = None) -> dict:
    return {
        "name":        name_override or form.get("name", ""),
        "type":        form.get("type", ConnectorType.proxmox.value),
        "config_ssh":  [v for k, v in form.multi_items() if k == "config_ssh"],
        "config_path": [v for k, v in form.multi_items() if k == "config_path"],
        "config_url":  [v for k, v in form.multi_items() if k == "config_url"],
    }


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    all_pipelines = utils.load_all_pipelines()
    all_jobs = jobs.list_jobs()

    # first entry per pipeline (list_jobs returns desc by created_at)
    latest_jobs: dict[str, dict] = {}
    for job in all_jobs:
        latest_jobs.setdefault(job["pipeline_name"], job)

    return templates.TemplateResponse(request= request, context={
        "request": request,
        "pipelines": all_pipelines,
        "latest_jobs": latest_jobs,
        "recent_jobs": all_jobs[:15],
    }, name="index.html")


@router.get("/pipeline/new", response_class=HTMLResponse)
def new_pipeline_page(request: Request):
    return templates.TemplateResponse(request=request, name="pipeline_form.html", context={
        **_FORM_BASE,
        "request": request,
        "editing": False,
        "form_data": {
            "name": "", "group": "default", "cron": "0 * * * *",
            "runner": RunnerType.proxmox_ct.value, "connectors": [],
        },
        "steps": [],
        "all_step_ids": [],
        "available_connectors": _available_connectors(),
        "errors": None,
    })


@router.post("/pipeline/new", response_class=HTMLResponse)
async def create_pipeline(request: Request):
    form = await request.form()
    try:
        group, pipeline = _parse_pipeline_form(form)
    except (ValidationError, ValueError) as exc:
        errors = [f"{' → '.join(str(x) for x in e['loc'])}: {e['msg']}" for e in (exc.errors() if hasattr(exc, 'errors') else [])] or [str(exc)]
        steps = _steps_from_form(form)
        return templates.TemplateResponse(request=request, name="pipeline_form.html", status_code=422, context={
            **_FORM_BASE,
            "request": request,
            "editing": False,
            "form_data": {
                "name": form.get("name", ""), "group": form.get("group", "default"),
                "cron": form.get("cron", ""), "runner": form.get("runner", ""),
                "connectors": [v for k, v in form.multi_items() if k == "connectors"],
            },
            "steps": steps,
            "all_step_ids": [s["id"] for _, s in steps if s["id"]],
            "available_connectors": _available_connectors(),
            "errors": errors,
        })
    _storage.save_pipeline(pipeline, group)
    return RedirectResponse(f"/pipeline/{pipeline.name}", status_code=303)


@router.get("/pipeline/{name}/edit", response_class=HTMLResponse)
def edit_pipeline_page(request: Request, name: str):
    pipeline, group = utils.get_pipeline_or_404(name, None)
    steps = [(i, {
        "id":            s.id,
        "exec":          s.exec,
        "check_method":  s.check_method.value,
        "check_pattern": s.check_pattern or "",
        "if_failed":     s.if_failed or "",
        "requires":      s.requires,
    }) for i, s in enumerate(pipeline.pipeline)]
    return templates.TemplateResponse(request=request, name="pipeline_form.html", context={
        **_FORM_BASE,
        "request": request,
        "editing": True,
        "form_data": {
            "name": pipeline.name, "group": group,
            "cron": pipeline.cron, "runner": pipeline.runner.value,
            "connectors": pipeline.connectors,
        },
        "steps": steps,
        "all_step_ids": [s.id for s in pipeline.pipeline],
        "available_connectors": _available_connectors(),
        "errors": None,
    })


@router.post("/pipeline/{name}/edit", response_class=HTMLResponse)
async def update_pipeline_route(request: Request, name: str):
    form = await request.form()
    try:
        group, pipeline = _parse_pipeline_form(form)
    except (ValidationError, ValueError) as exc:
        errors = [f"{' → '.join(str(x) for x in e['loc'])}: {e['msg']}" for e in (exc.errors() if hasattr(exc, 'errors') else [])] or [str(exc)]
        steps = _steps_from_form(form)
        return templates.TemplateResponse(request=request, name="pipeline_form.html", status_code=422, context={
            **_FORM_BASE,
            "request": request,
            "editing": True,
            "form_data": {
                "name": name, "group": form.get("group", "default"),
                "cron": form.get("cron", ""), "runner": form.get("runner", ""),
                "connectors": [v for k, v in form.multi_items() if k == "connectors"],
            },
            "steps": steps,
            "all_step_ids": [s["id"] for _, s in steps if s["id"]],
            "available_connectors": _available_connectors(),
            "errors": errors,
        })
    _storage.update_pipeline(pipeline, group)
    return RedirectResponse(f"/pipeline/{pipeline.name}", status_code=303)


@router.get("/step-row", response_class=HTMLResponse)
def step_row_fragment(request: Request, index: int = 0, steps: str = ""):
    all_step_ids = [s.strip() for s in steps.split(",") if s.strip()]
    return templates.TemplateResponse(request=request, name="_step_row.html", context={
        "request": request,
        "idx": index,
        "step": None,
        "check_methods": list(CheckMethod),
        "all_step_ids": all_step_ids,
    })


@router.get("/pipeline/{name}", response_class=HTMLResponse)
def pipeline_page(request: Request, name: str):
    pipeline, group = utils.get_pipeline_or_404(name, None)
    columns = _compute_columns(pipeline.pipeline)
    edges = _build_edges(pipeline.pipeline)
    pipeline_jobs = [j for j in jobs.list_jobs() if j["pipeline_name"] == name]

    return templates.TemplateResponse(request=request, name="pipeline.html", context={
        "request": request,
        "pipeline": pipeline,
        "group": group,
        "columns": columns,
        "edges": edges,
        "pipeline_jobs": pipeline_jobs[:20],
    })


@router.post("/job/{job_id}/delete", response_class=HTMLResponse)
async def delete_job_route(request: Request, job_id: UUID):
    if not jobs.delete_job(job_id):
        raise HTTPException(status_code=409, detail="Job cannot be deleted (not found or still running).")
    return RedirectResponse("/", status_code=303)


@router.post("/pipeline/{name}/delete", response_class=HTMLResponse)
async def delete_pipeline_route(request: Request, name: str):
    pipeline, group = utils.get_pipeline_or_404(name, None)
    _storage.delete_pipeline(name, group)
    return RedirectResponse("/", status_code=303)


@router.get("/connectors", response_class=HTMLResponse)
def connectors_page(request: Request):
    manager = _storage.load_manager()
    return templates.TemplateResponse(request=request, name="connectors.html", context={
        "request": request,
        "connectors": list(manager),
    })


@router.get("/connector/new", response_class=HTMLResponse)
def new_connector_page(request: Request):
    return templates.TemplateResponse(request=request, name="connector_form.html", context={
        "request": request,
        "editing": False,
        "connector_types": list(ConnectorType),
        "form_data": {
            "name": "", "type": ConnectorType.proxmox.value,
            "config_ssh": [], "config_path": [], "config_url": [],
        },
        "errors": None,
    })


@router.post("/connector/new", response_class=HTMLResponse)
async def create_connector_web(request: Request):
    form = await request.form()
    try:
        connector = _parse_connector_form(form)
    except (ValidationError, ValueError) as exc:
        errors = [f"{' → '.join(str(x) for x in e['loc'])}: {e['msg']}" for e in (exc.errors() if hasattr(exc, 'errors') else [])] or [str(exc)]
        return templates.TemplateResponse(request=request, name="connector_form.html", status_code=422, context={
            "request": request,
            "editing": False,
            "connector_types": list(ConnectorType),
            "form_data": _connector_form_data(form),
            "errors": errors,
        })
    manager = _storage.load_manager()
    if connector.name in manager:
        return templates.TemplateResponse(request=request, name="connector_form.html", status_code=409, context={
            "request": request,
            "editing": False,
            "connector_types": list(ConnectorType),
            "form_data": _connector_form_data(form),
            "errors": [f"Connector '{connector.name}' already exists."],
        })
    manager.add(connector)
    _storage.save_manager(manager)
    return RedirectResponse("/connectors", status_code=303)


@router.get("/connector/{name}/edit", response_class=HTMLResponse)
def edit_connector_page(request: Request, name: str):
    manager = _storage.load_manager()
    connector = utils.get_connector_or_404(manager, name)
    return templates.TemplateResponse(request=request, name="connector_form.html", context={
        "request": request,
        "editing": True,
        "connector_types": list(ConnectorType),
        "form_data": {
            "name": connector.name, "type": connector.type.value,
            "config_ssh": connector.config_ssh,
            "config_path": connector.config_path,
            "config_url": connector.config_url,
        },
        "errors": None,
    })


@router.post("/connector/{name}/edit", response_class=HTMLResponse)
async def update_connector_web(request: Request, name: str):
    form = await request.form()
    try:
        connector = _parse_connector_form(form)
    except (ValidationError, ValueError) as exc:
        errors = [f"{' → '.join(str(x) for x in e['loc'])}: {e['msg']}" for e in (exc.errors() if hasattr(exc, 'errors') else [])] or [str(exc)]
        return templates.TemplateResponse(request=request, name="connector_form.html", status_code=422, context={
            "request": request,
            "editing": True,
            "connector_types": list(ConnectorType),
            "form_data": _connector_form_data(form, name_override=name),
            "errors": errors,
        })
    manager = _storage.load_manager()
    utils.get_connector_or_404(manager, name)
    manager.remove(name)
    manager.add(connector)
    _storage.save_manager(manager)
    return RedirectResponse("/connectors", status_code=303)


@router.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: UUID):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    pipeline, group = utils.get_pipeline_or_404(job["pipeline_name"], None)
    columns = _compute_columns(pipeline.pipeline)
    edges = _build_edges(pipeline.pipeline)

    target_results = [
        {
            "target_id":   result["target_id"],
            "target_name": result["target_name"],
            "t_status":    result["status"],
            "duration":    result["duration"],
            "step_results": {s["step_id"]: s for s in result["steps"]},
        }
        for result in job["results"]
    ]

    status_counts = {"green": 0, "orange": 0, "red": 0}
    for tr in target_results:
        if tr["t_status"] in status_counts:
            status_counts[tr["t_status"]] += 1

    is_live = str(job["status"].value) in ("pending", "running")

    return templates.TemplateResponse(
        request=request,
        name="job.html",
        context={
        "request": request,
        "job": job,
        "pipeline": pipeline,
        "group": group,
        "columns": columns,
        "edges": edges,
        "target_results": target_results,
        "status_counts": status_counts,
        "is_live": is_live,
    })
