from uuid import UUID

from fastapi import Request, HTTPException, APIRouter, BackgroundTasks
from fastapi.params import Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from src.api import utils
from src.api.website.utils import compute_columns, build_edges, templates
from src.classes.connectors import Manager
from src.core import jobs
from src.core.database import JobSource

# Substrings that indicate an SSH/authentication failure in a crash traceback.
_SSH_MARKERS = ("paramiko", "AuthenticationException", "NoValidConnectionsError", "ssh_exception")

router = APIRouter(tags=["job"])


@router.post("/run/{name}")
def web_start_job(
    name: str,
    background_tasks: BackgroundTasks,
    manager: Manager = Depends(utils.get_manager),
):
    utils.get_pipeline_or_404(name, None)
    job_id = jobs.create_job(pipeline_name=name, source=JobSource.manual)
    background_tasks.add_task(utils.execute_job, job_id, name, manager)
    return {"job_id": str(job_id)}


@router.post("/{job_id}/retry")
def web_retry_job(
    job_id: UUID,
    background_tasks: BackgroundTasks,
    manager: Manager = Depends(utils.get_manager),
):
    pipeline_name = jobs.retry_job(job_id)
    if pipeline_name is None:
        raise HTTPException(status_code=409, detail="Job is not retryable.")
    new_job_id = jobs.create_job(pipeline_name=pipeline_name)
    background_tasks.add_task(utils.execute_job, new_job_id, pipeline_name, manager)
    return {"job_id": str(new_job_id)}


@router.post("/{job_id}/cancel", status_code=204)
def web_cancel_job(job_id: UUID):
    if not jobs.cancel_job(job_id):
        raise HTTPException(status_code=409, detail="Job is not cancellable.")


@router.post("/{job_id}/delete", response_class=HTMLResponse)
async def delete_job_route(request: Request, job_id: UUID):
    if not jobs.delete_job(job_id):
        raise HTTPException(status_code=409, detail="Job cannot be deleted (not found or still running).")
    return RedirectResponse("/", status_code=303)


@router.get("/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: UUID):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    pipeline, group = utils.get_pipeline_or_404(job["pipeline_name"], None)
    columns = compute_columns(pipeline.pipeline)
    edges = build_edges(pipeline.pipeline)
    non_leaf_branches = frozenset(
        (req.step, req.branch)
        for step in pipeline.pipeline
        for req in step.requires
    )

    target_results = [
        {
            "target_id":    result["target_id"],
            "target_name":  result["target_name"],
            "t_status":     result["status"],
            "duration":     result["duration"],
            "step_results": {s["step_id"]: s for s in result["steps"]},
        }
        for result in job["results"]
    ]

    from src.api.website.utils import signal_group
    status_counts = {"green": 0, "orange": 0, "red": 0}
    for tr in target_results:
        status_counts[signal_group(tr["t_status"])] += 1

    is_live = str(job["status"].value) in ("pending", "running")
    crash_reason = job.get("crash_reason")
    is_ssh_error = bool(crash_reason and any(m in crash_reason for m in _SSH_MARKERS))

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
            "non_leaf_branches": non_leaf_branches,
            "status_counts": status_counts,
            "is_live": is_live,
            "crash_reason": crash_reason,
            "is_ssh_error": is_ssh_error,
        },
    )
