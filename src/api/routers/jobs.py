from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.params import Depends, Query
from pydantic import BaseModel

from src.api.security import require_api_key
from src.api import utils
from src.classes.connectors import Manager
from src.core import jobs
from src.core.database import JobSource

router = APIRouter(prefix="/jobs", tags=["jobs", "runs"], dependencies=[Depends(require_api_key)])


class StepResultResponse(BaseModel):
    step_id: str
    signal: str
    stdout: str
    stderr: str
    branch: int
    skipped: bool
    duration: float


class PipelineResultResponse(BaseModel):
    target_id: str
    pipeline_name: str
    status: str
    steps: list[StepResultResponse]


class JobResponse(BaseModel):
    id: UUID
    pipeline_name: str
    status: str
    source: str
    created_at: datetime
    results: list[PipelineResultResponse]


class JobSummaryResponse(BaseModel):
    id: UUID
    pipeline_name: str
    status: str
    source: str
    created_at: datetime


class JobCreatedResponse(BaseModel):
    job_id: UUID


@router.post("/{name}", response_model=JobCreatedResponse, status_code=202)
def start_pipeline(
    name: str,
    background_tasks: BackgroundTasks,
    manager: Manager = Depends(utils.get_manager),
    group: Annotated[str | None, Query()] = None,
):
    utils.get_pipeline_or_404(name, group)
    job_id = jobs.create_job(pipeline_name=name, source=JobSource.manual)
    background_tasks.add_task(utils.execute_job, job_id, name, manager)
    return JobCreatedResponse(job_id=job_id)


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: UUID):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


@router.get("/", response_model=list[JobSummaryResponse])
def get_jobs():
    return jobs.list_jobs()


@router.post("/{job_id}/cancel", status_code=204)
def cancel_job(job_id: UUID):
    if not jobs.cancel_job(job_id):
        raise HTTPException(status_code=409, detail="Job is not cancellable (already terminal or not found).")


@router.post("/{job_id}/retry", response_model=JobCreatedResponse, status_code=202)
def retry_job(
    job_id: UUID,
    background_tasks: BackgroundTasks,
    manager: Manager = Depends(utils.get_manager),
):
    pipeline_name = jobs.retry_job(job_id)
    if pipeline_name is None:
        raise HTTPException(status_code=409, detail="Job is not retryable (must be failed or cancelled).")
    new_job_id = jobs.create_job(pipeline_name=pipeline_name)
    background_tasks.add_task(utils.execute_job, new_job_id, pipeline_name, manager)
    return JobCreatedResponse(job_id=new_job_id)
