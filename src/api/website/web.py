from uuid import UUID

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from src.api import utils
from src.api.website.utils import (compute_columns, build_edges, connector_form_data, parse_connector_form, templates,
                                   list_scripts)
from src.classes.pipeline import CheckMethod
from src.core import jobs

router = APIRouter(tags=["web"])

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



@router.get("/step-row", response_class=HTMLResponse)
def step_row_fragment(request: Request, index: int = 0, steps: str = ""):
    all_step_ids = [s.strip() for s in steps.split(",") if s.strip()]
    return templates.TemplateResponse(request=request, name="_step_row.html", context={
        "request": request,
        "idx": index,
        "step": None,
        "check_methods": list(CheckMethod),
        "all_step_ids": all_step_ids,
        "available_scripts": list_scripts(),
    })
