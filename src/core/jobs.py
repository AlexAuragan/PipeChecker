from datetime import datetime, timezone, timedelta
from uuid import UUID

from sqlmodel import Session, select

from src.classes.results import PipelineResult
from src.core.database import (
    ArchivedRun, ArchivedStepResult, Job, JobSource, JobStatus,
    LivePipelineResult, LiveStepResult, engine,
)

MAX_OUTPUT_LEN = 4096
_cancelled: set[UUID] = set()


def is_cancelled(job_id: UUID) -> bool:
    return job_id in _cancelled


def create_job(uuid: UUID = None, pipeline_name: str = None, source: JobSource = JobSource.manual) -> UUID:
    with Session(engine) as session:
        job = Job(pipeline_name=pipeline_name, uuid=uuid, source=source)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


def set_job_status(job_id: UUID, status: JobStatus, crash_reason: str | None = None) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise KeyError(f"Job {job_id} not found.")
        job.status = status
        if crash_reason is not None:
            job.crash_reason = crash_reason
        session.add(job)
        session.commit()


def write_pipeline_result(job_id: UUID, result: PipelineResult) -> None:
    with Session(engine) as session:
        pr = LivePipelineResult(
            job_id=job_id,
            target_id=str(result.target.id),
            target_name=result.target.name,
            pipeline_name=result.pipeline_name,
            status=result.status,
            duration=result.duration,
        )
        session.add(pr)
        session.flush()

        for step in result.steps.values():
            session.add(LiveStepResult(
                pipeline_result_id=pr.id,
                step_id=step.step_id,
                success=step.success,
                stdout=step.stdout[:MAX_OUTPUT_LEN],
                stderr=step.stderr[:MAX_OUTPUT_LEN],
                tried_fix=step.tried_fix,
                skipped=step.skipped,
                duration=step.duration,
            ))
        session.commit()


def get_job(job_id: UUID) -> dict | None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            return None
        return {
            "id": job.id,
            "pipeline_name": job.pipeline_name,
            "status": job.status,
            "source": job.source,
            "created_at": job.created_at,
            "crash_reason": job.crash_reason,
            "results": [
                {
                    "target_id": pr.target_id,
                    "target_name": pr.target_name or pr.target_id,
                    "pipeline_name": pr.pipeline_name,
                    "status": pr.status,
                    "duration": pr.duration,
                    "steps": [
                        {
                            "step_id": s.step_id,
                            "success": s.success,
                            "stdout": s.stdout,
                            "stderr": s.stderr,
                            "tried_fix": s.tried_fix,
                            "skipped": s.skipped,
                            "duration": s.duration,
                        }
                        for s in pr.steps
                    ],
                }
                for pr in job.results
            ],
        }


_TERMINAL = (JobStatus.completed, JobStatus.failed, JobStatus.crashed, JobStatus.cancelled)
_STALE_AFTER = timedelta(hours=1)


def crash_stale_jobs(crash_all_running: bool = False) -> int:
    """
    Mark jobs as crashed when:
    - crash_all_running=True: ALL running jobs (server just restarted mid-execution)
    - Always: pending or running jobs whose created_at is older than _STALE_AFTER

    Returns the number of jobs affected.
    """
    cutoff = datetime.now(timezone.utc) - _STALE_AFTER
    with Session(engine) as session:
        to_crash: list[Job] = []

        if crash_all_running:
            to_crash = list(session.exec(
                select(Job).where(Job.status == JobStatus.running)
            ).all())

        stale = session.exec(
            select(Job).where(
                Job.status.in_([JobStatus.running, JobStatus.pending]),
                Job.created_at < cutoff,
            )
        ).all()
        seen = {j.id for j in to_crash}
        to_crash.extend(j for j in stale if j.id not in seen)

        for job in to_crash:
            job.status = JobStatus.crashed
            session.add(job)
        session.commit()
        return len(to_crash)


def archive_old_jobs() -> None:
    """Move terminal jobs older than 24h into the archive tables."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    with Session(engine) as session:
        old_jobs = session.exec(
            select(Job).where(
                Job.created_at < cutoff,
                Job.status.in_([JobStatus.completed, JobStatus.failed, JobStatus.crashed]),
            )
        ).all()

        for job in old_jobs:
            for pr in job.results:
                last = session.exec(
                    select(ArchivedRun)
                    .where(
                        ArchivedRun.pipeline_name == pr.pipeline_name,
                        ArchivedRun.target_id == pr.target_id,
                    )
                    .order_by(ArchivedRun.ran_at.desc())
                ).first()

                changed = last is None or last.status != pr.status
                archived = ArchivedRun(
                    pipeline_name=pr.pipeline_name,
                    target_id=pr.target_id,
                    ran_at=job.created_at,
                    status=pr.status,
                    changed=changed,
                    duration=pr.duration,
                )
                session.add(archived)
                session.flush()

                if changed:
                    for step in pr.steps:
                        session.add(ArchivedStepResult(
                            archived_run_id=archived.id,
                            step_id=step.step_id,
                            success=step.success,
                            stdout=step.stdout,
                            stderr=step.stderr,
                            tried_fix=step.tried_fix,
                            skipped=step.skipped,
                            duration=step.duration,
                        ))

            session.delete(job)

        session.commit()

def cancel_job(job_id: UUID) -> bool:
    """Returns False if the job doesn't exist or is already terminal."""
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None or job.status in _TERMINAL:
            return False
        job.status = JobStatus.cancelled
        session.add(job)
        session.commit()
    _cancelled.add(job_id)
    return True


def retry_job(job_id: UUID) -> str | None:
    """Creates a new job for the same pipeline. Returns None if original not found or not retryable."""
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None or job.status not in (JobStatus.failed, JobStatus.crashed, JobStatus.cancelled):
            return None
        return job.pipeline_name


def delete_job(job_id: UUID) -> bool:
    """Permanently delete a terminal job. Returns False if not found or still running."""
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None or job.status not in _TERMINAL:
            return False
        session.delete(job)
        session.commit()
    _cancelled.discard(job_id)
    return True


def delete_cancelled_jobs() -> None:
    """Permanently delete all cancelled jobs."""
    with Session(engine) as session:
        cancelled_jobs = session.exec(
            select(Job).where(Job.status == JobStatus.cancelled)
        ).all()
        ids = {job.id for job in cancelled_jobs}
        for job in cancelled_jobs:
            session.delete(job)
        session.commit()
    _cancelled.difference_update(ids)


def list_jobs() -> list[dict]:
    with Session(engine) as session:
        jobs = session.exec(select(Job).order_by(Job.created_at.desc())).all()
        return [
            {
                "id": job.id,
                "pipeline_name": job.pipeline_name,
                "status": job.status,
                "source": job.source,
                "created_at": job.created_at,
            } for job in jobs
        ]