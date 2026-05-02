import asyncio
import os
import sys
import traceback
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, HTTPException, Request, status
from watchfiles import awatch

from src import config
from src.classes.connectors import Connector, Manager
from src.classes.pipeline import Pipeline
from src.core import storage

scheduler = AsyncIOScheduler()


def make_scheduled_job(app: FastAPI, pipe: Pipeline):
    """Return an async callable that runs the given pipeline as a cron job."""

    async def job():
        manager = app.state.manager
        if manager is None:
            print(f"Skipping {pipe.name}: manager not ready")
            return
        from src.core.database import JobSource

        job_id = jobs.create_job(pipeline_name=pipe.name, source=JobSource.cron)
        await execute_job(job_id, pipe.name, manager)

    return job


async def initial_load(app: FastAPI) -> None:
    """Load connectors in a background thread and mark the app as ready."""
    try:
        app.state.manager = await asyncio.to_thread(load_and_init)
        print("Connectors loaded")
    except Exception as e:
        print(f"Failed to load connectors: {e}")
        app.state.manager = Manager(autoload=False)  # empty — jobs run with no targets
    finally:
        app.state.ready = True


def _check_credentials() -> None:
    missing = []
    if not os.getenv("PIPECHECKER_API_KEY_HASH"):
        missing.append("PIPECHECKER_API_KEY_HASH  (run: python cli.py generate-key)")
    if not os.getenv("PIPECHECKER_WEB_PASSWORD_HASH"):
        missing.append(
            "PIPECHECKER_WEB_PASSWORD_HASH  (run: python cli.py generate-web-password)"
        )
    if missing:
        print("ERROR: The following required environment variables are not set:")
        for m in missing:
            print(f"  • {m}")
        print("Run 'python cli.py setup' to generate and save all credentials at once.")
        sys.exit(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_credentials()

    app.state.manager = None
    app.state.ready = False

    # Mark any jobs that were running when the server last died as crashed.
    from src.core import jobs as _jobs

    n = _jobs.crash_stale_jobs(crash_all_running=True)
    if n:
        print(f"Marked {n} orphaned job(s) as crashed (server restart)")

    load_task = asyncio.create_task(initial_load(app))
    watcher_task = asyncio.create_task(watch_config(app))
    archive_task = asyncio.create_task(_archive_loop())

    for group_pipes in storage.load_pipelines().values():
        for pipe in group_pipes.values():
            scheduler.add_job(
                make_scheduled_job(app, pipe),
                CronTrigger.from_crontab(pipe.cron),
                id=pipe.name,
            )
    scheduler.start()

    yield

    load_task.cancel()
    watcher_task.cancel()
    archive_task.cancel()
    for task in (load_task, watcher_task, archive_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    scheduler.shutdown()


def load_and_init() -> Manager:
    """Load connector configs from disk. Targets are loaded lazily on first use."""
    return storage.load_manager()


def _ensure_targets_loaded(pipeline: Pipeline, manager: Manager) -> None:
    """Load targets for any connector in this pipeline that hasn't been loaded yet."""
    for connector_name in pipeline.connectors:
        if connector_name not in manager:
            continue
        connector = manager.get(connector_name)
        if not connector.is_loaded:
            connector.load_targets()


def get_manager(request: Request) -> Manager:
    """FastAPI dependency: return the app-level Manager or raise 503 if not ready."""
    if not request.app.state.ready:
        raise HTTPException(status_code=503, detail="Still loading connectors")
    return request.app.state.manager

ManagerDep = Annotated[Manager, Depends(get_manager)]

async def watch_config(app: FastAPI):
    """Background task: reload the manager whenever the connector config file changes."""
    async for _changes in awatch(config.CONNECTOR_FILE):
        print("Config file changed, reloading connectors...")
        try:
            new_manager = await asyncio.to_thread(load_and_init)
            app.state.manager = new_manager
            print(f"Reload complete: {len(list(new_manager))} connectors")
        except Exception as e:
            print(f"Reload failed, keeping old config: {e}")


async def _archive_loop():
    """Background task: run archival, stale-job cleanup, and cancelled-job cleanup every hour."""
    from src.core import jobs

    while True:
        await asyncio.sleep(3600)
        try:
            n = await asyncio.to_thread(jobs.crash_stale_jobs, False)
            if n:
                print(f"Timed out {n} stale job(s) → crashed")
        except Exception as e:
            print(f"Stale job cleanup failed: {e}")
        try:
            await asyncio.to_thread(jobs.archive_old_jobs)
        except Exception as e:
            print(f"Archival failed: {e}")
        try:
            await asyncio.to_thread(jobs.delete_cancelled_jobs)
        except Exception as e:
            print(f"Cancelled job cleanup failed: {e}")


async def reload_manager(app: FastAPI) -> None:
    """Reload the connector manager from disk, keeping the old config on failure."""
    try:
        # state.ready stays True: the old manager is always available as a fallback
        new_manager = await asyncio.to_thread(load_and_init)
        app.state.manager = new_manager
        print(f"Reload complete: {len(list(new_manager))} connectors")
    except Exception as e:
        print(f"Reload failed, keeping old config: {e}")


## Pipelines


def load_all_pipelines() -> dict[str, Pipeline]:
    """Flatten all pipeline groups into a single dict keyed by pipeline name."""
    return {
        name: pipe
        for group in storage.load_pipelines().values()
        for name, pipe in group.items()
    }


## Connectors


def get_connector_or_404(manager: Manager, name: str) -> Connector:
    try:
        return manager.get(name)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector '{name}' not found.",
        )


def get_pipeline_or_404(name: str, group: str | None) -> tuple[Pipeline, str]:
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
# Deferred to avoid a circular import (jobs/run import from classes which import from api).
from typing import TYPE_CHECKING  # noqa: E402

from src.core import jobs, run  # noqa: E402
from src.core.database import JobStatus  # noqa: E402

if TYPE_CHECKING:
    from src.classes.results import PipelineResult


def _fire_alerts(result: "PipelineResult", job_id: UUID) -> None:  # type: ignore[name-defined]
    """Check alert configs against a pipeline result and dispatch/record any matches."""
    from src.classes.alert import SentAlert

    try:
        alerts = storage.load_alerts()
        if not alerts:
            return
        for alert_cfg in alerts:
            if (
                alert_cfg.pipeline is not None
                and alert_cfg.pipeline != result.pipeline_name
            ):
                continue
            if result.status not in alert_cfg.on_signals:
                continue
            sent = SentAlert(
                alert_name=alert_cfg.name,
                pipeline_name=result.pipeline_name,
                target_id=str(result.target.id),
                target_name=result.target.name,
                signal=result.status,
                url=f"{config.BASE_URL}/job/{job_id}",
            )
            jobs.record_sent_alert(sent)
            if alert_cfg.connector:
                try:
                    connector = next(
                        (
                            c
                            for c in storage.load_alert_connectors()
                            if c.name == alert_cfg.connector
                        ),
                        None,
                    )
                    if connector:
                        connector.send(sent)
                    else:
                        print(f"Alert connector '{alert_cfg.connector}' not found")
                except Exception as e:
                    print(f"Alert connector '{alert_cfg.connector}' failed: {e}")
    except Exception as e:
        print(f"Alert firing failed for job {job_id}: {e}")


async def execute_job(job_id: UUID, pipeline_name: str, manager: Manager) -> None:
    """Run a pipeline in a background thread, updating the job status in the DB."""
    print(f"running job {job_id} on pipeline {pipeline_name}")
    jobs.set_job_status(job_id, JobStatus.running)
    try:
        pipelines = load_all_pipelines()
        if pipeline_name not in pipelines:
            jobs.set_job_status(job_id, JobStatus.failed)
            return

        pipeline = pipelines[pipeline_name]

        needs_loading = any(
            not manager.get(c).is_loaded for c in pipeline.connectors if c in manager
        )
        if needs_loading:
            jobs.set_job_phase(job_id, "Loading targets…")
            await asyncio.to_thread(_ensure_targets_loaded, pipeline, manager)
            jobs.set_job_phase(job_id, None)

        def on_result(r):
            jobs.write_pipeline_result(job_id, r)
            _fire_alerts(r, job_id)

        await asyncio.to_thread(
            run.run_pipeline,
            pipeline,
            manager,
            on_result,
            lambda: jobs.is_cancelled(job_id),
        )
        if jobs.is_cancelled(job_id):
            return
        jobs.set_job_status(job_id, JobStatus.completed)
    except Exception as e:
        tb = traceback.format_exc()
        jobs.set_job_status(job_id, JobStatus.crashed, crash_reason=tb)
        print(f"Job {job_id} crashed with exception: {e}")
