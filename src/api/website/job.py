from uuid import UUID

from fastapi import Request, HTTPException, APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from src.api import utils
from src.api.website.utils import compute_columns, build_edges, templates
from src.core import jobs

router = APIRouter(tags=["job"])

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
