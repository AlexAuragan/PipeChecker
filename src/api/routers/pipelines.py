from typing import Annotated

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, HTTPException, Request, status, Query, Depends
from pydantic import BaseModel, ValidationError

from src.api.security import require_api_key
from src.api.utils import get_pipeline_or_404, make_scheduled_job, scheduler
from src.classes import pipeline as p
from src.classes.pipeline import CheckMethod
from src.core import storage

router = APIRouter(prefix="/pipelines", tags=["pipelines"], dependencies=[Depends(require_api_key)])

## Schema

class PipelineStepPatch(BaseModel):
    exec: str | None = None
    check_method: CheckMethod | None = None
    check_pattern: str | None = None
    if_failed: str | None = None
    requires: list[str] = []

## Routes - Pipelines

@router.get("", response_model=list[p.Pipeline])
def list_pipeline():
    pipes = []
    for _pipes in storage.load_pipelines().values():
        pipes += _pipes.values()
    return pipes

@router.get("/{name}", response_model=p.Pipeline)
def get_pipeline(name: str, group: Annotated[str | None, Query()] = None):
    return get_pipeline_or_404(name, group)[0]

@router.post("", response_model=p.Pipeline, status_code=status.HTTP_201_CREATED)
def create_pipeline(request: Request, body: p.Pipeline, group: Annotated[str | None, Query()] = None):
    try:
        get_pipeline_or_404(body.name, group)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Pipeline '{body.name}' already exists.",
        )
    except HTTPException as e:
        if e.status_code != status.HTTP_404_NOT_FOUND:
            raise

    storage.save_pipeline(body, group=group or body.name)
    scheduler.add_job(make_scheduled_job(request.app, body), CronTrigger.from_crontab(body.cron), id=body.name)
    return body

@router.put("/{name}", response_model=p.Pipeline)
def replace_pipeline(name: str, body: p.Pipeline, group: Annotated[str | None, Query()] = None):
    pipe, group = get_pipeline_or_404(name, group)
    if body.name != name:
        raise HTTPException(status_code=400, detail="Body name must match path name.")
    storage.update_pipeline(body, group=group or name)
    return body


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pipeline(name: str, group: Annotated[str | None, Query()] = None):
    _, resolved_group = get_pipeline_or_404(name, group)
    storage.delete_pipeline(name, group=resolved_group)
    scheduler.remove_job(job_id=name)

## Route - Pipeline steps

@router.get("/{name}/steps", response_model=list[p.PipelineStep])
def list_steps(name: str, group: Annotated[str | None, Query()] = None):
    pipe, group = get_pipeline_or_404(name, group)
    return pipe.pipeline

@router.post("/{name}/steps", response_model=p.Pipeline)
def add_step(name: str, body: p.PipelineStep, group: Annotated[str | None, Query()] = None):
    pipe, group = get_pipeline_or_404(name, group)
    assert isinstance(pipe, p.Pipeline)
    curr_ids = [step.id for step in pipe.pipeline]
    if body.id in curr_ids:
        raise HTTPException(status_code=422, detail="Step id already in pipeline")
    if any(r not in curr_ids for r in body.requires):
        raise HTTPException(status_code=422, detail="Step requires a non existing step id")
    try:
        updated = p.Pipeline(name=pipe.name, pipeline=pipe.pipeline + [body], connectors=pipe.connectors, runner=pipe.runner, cron=pipe.cron)
    except ValidationError as e:
        messages = [err["msg"] for err in e.errors()]
        raise HTTPException(status_code=400, detail=messages)
    storage.update_pipeline(updated, group=group or name)
    scheduler.reschedule_job(job_id=name, trigger=CronTrigger.from_crontab(pipe.cron))
    return updated

@router.patch("/{name}/steps/{step_id}", response_model=p.Pipeline)
def edit_step(name: str, step_id: str, body: PipelineStepPatch, group: Annotated[str | None, Query()] = None):
    pipe, group = get_pipeline_or_404(name, group)
    step = next((s for s in pipe.pipeline if s.id == step_id), None)
    curr_ids = [step.id for step in pipe.pipeline]
    if step is None:
        raise HTTPException(status_code=404, detail=f"Step '{step_id}' not found in pipeline {name}.")
    if any(r not in curr_ids for r in body.requires):
        raise HTTPException(status_code=422, detail="Step requires a non existing step id")
    patched = step.model_copy(update=body.model_dump(exclude_none=True))
    new_steps = [patched if step.id == step_id else step for step in pipe.pipeline]
    try:
        updated = p.Pipeline(name=pipe.name, pipeline=new_steps, connectors=pipe.connectors, runner=pipe.runner, cron=pipe.cron)
    except ValidationError as e:
        messages = [err["msg"] for err in e.errors()]
        raise HTTPException(status_code=400, detail=messages)
    storage.update_pipeline(updated, group=group or name)
    return updated

@router.delete("/{name}/steps/{step_id}", response_model=p.Pipeline)
def remove_step(name: str, step_id: str, group: Annotated[str | None, Query()] = None):
    pipe, group = get_pipeline_or_404(name, group)
    new_steps = [s for s in pipe.pipeline if s.id != step_id]
    if len(new_steps) == len(pipe.pipeline):
        raise HTTPException(status_code=404, detail=f"Step '{step_id}' not found.")
    if len(new_steps) == 0:
        raise HTTPException(status_code=422, detail=f"Pipeline cannot be empty.")
    try:
        updated = p.Pipeline(name=pipe.name, pipeline=new_steps, connectors=pipe.connectors, runner=pipe.runner, cron=pipe.cron)
    except ValidationError as e:
        messages = [err["msg"] for err in e.errors()]
        code = 400
        if any("requires unknown" in m for m in messages):
            code = 409
        raise HTTPException(status_code=code, detail=messages)
    storage.update_pipeline(updated, group=group or name)
    return updated