import asyncio
from contextlib import asynccontextmanager
from typing import Tuple

from fastapi import FastAPI, Request, HTTPException, status
from watchfiles import awatch

from src import config
from src.classes.connectors import Manager, Connector
from src.classes.pipeline import Pipeline
from src.core import storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.manager = None
    app.state.ready = False

    async def initial_load():
        try:
            app.state.manager = await asyncio.to_thread(load_and_init)
            app.state.ready = True
            print("Connectors loaded")
        except Exception as e:
            print(f"Failed to load connectors: {e}")

    load_task = asyncio.create_task(initial_load())
    watcher_task = asyncio.create_task(watch_config(app))
    archive_task = asyncio.create_task(_archive_loop())

    yield  # server starts accepting connections immediately

    load_task.cancel()
    watcher_task.cancel()
    archive_task.cancel()
    for task in (load_task, watcher_task, archive_task):
        try:
            await task
        except asyncio.CancelledError:
            pass


def load_and_init() -> Manager:
    m = storage.load_manager()
    m.load_targets()
    return m


def get_manager(request: Request) -> Manager:
    if not request.app.state.ready:
        raise HTTPException(status_code=503, detail="Still loading connectors")
    return request.app.state.manager

async def watch_config(app: FastAPI):
    """Background task that reloads the manager when the config file changes."""
    async for _changes in awatch(config.CONNECTOR_FILE):
        print(f"Config file changed, reloading connectors...")
        try:
            new_manager = await asyncio.to_thread(load_and_init)
            app.state.manager = new_manager
            print(f"Reload complete: {len(list(new_manager))} connectors")
        except Exception as e:
            print(f"Reload failed, keeping old config: {e}")

async def _archive_loop():
    """Run archival and cancelled-job cleanup every hour."""
    from src.core import jobs
    while True:
        await asyncio.sleep(3600)
        try:
            await asyncio.to_thread(jobs.archive_old_jobs)
        except Exception as e:
            print(f"Archival failed: {e}")
        try:
            await asyncio.to_thread(jobs.delete_cancelled_jobs)
        except Exception as e:
            print(f"Cancelled job cleanup failed: {e}")

async def reload_manager(app: FastAPI):
    try:
        # We don't concern ourselves with state.ready since the old config is always available
        new_manager = await asyncio.to_thread(load_and_init)
        app.state.manager = new_manager
        print(f"Reload complete: {len(list(new_manager))} connectors")
    except Exception as e:
        print(f"Reload failed, keeping old config: {e}")

## Pipelines
def load_all_pipelines() -> dict[str, Pipeline]:
    """Flatten all pipeline groups into a single dict keyed by pipeline name."""
    return {name: pipe for group in storage.load_pipelines().values() for name, pipe in group.items()}

## Connectors
def get_connector_or_404(manager: Manager, name: str) -> Connector:
    try:
        return manager.get(name)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector '{name}' not found.",
        )

def get_pipeline_or_404(name: str, group: str | None) -> Tuple[Pipeline, str]:
    for g, pipes in storage.load_pipelines().items():
        if group and g != group:
            continue
        if pipe := pipes.get(name):
            return pipe, g
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Pipeline '{name}' not found.",
    )

## Jobs
from src.core import jobs, run
from src.core.database import JobStatus
from uuid import UUID

async def execute_job(job_id: UUID, pipeline_name: str, manager: Manager) -> None:
    jobs.set_job_status(job_id, JobStatus.running)
    try:
        pipelines = load_all_pipelines()
        if pipeline_name not in pipelines:
            jobs.set_job_status(job_id, JobStatus.failed)
            return
        await asyncio.to_thread(
            run.run_pipeline,
            pipelines[pipeline_name],
            manager,
            lambda r: jobs.write_pipeline_result(job_id, r),
            lambda: jobs.is_cancelled(job_id),
        )
        if jobs.is_cancelled(job_id):
            return
        jobs.set_job_status(job_id, JobStatus.completed)
    except Exception as e:
        jobs.set_job_status(job_id, JobStatus.failed)
        print(f"Job {job_id} failed with exception: {e}")
