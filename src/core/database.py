from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import model_validator
from sqlalchemy import inspect as sa_inspect, text
from sqlmodel import SQLModel, Field, Relationship, create_engine

from src import config


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"    # pre-execution failure (pipeline not found, etc.)
    crashed = "crashed"  # mid-execution exception or server-restart interruption
    cancelled = "cancelled"


class JobSource(str, Enum):
    manual = "manual"
    cron = "cron"
    event = "event"


class Job(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    pipeline_name: str | None = None
    status: JobStatus = JobStatus.pending
    source: JobSource = Field(default=JobSource.manual)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    results: list["LivePipelineResult"] = Relationship(
        back_populates="job",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    @model_validator(mode='after')
    def set_default_pipeline_name(self) -> 'Job':
        if self.pipeline_name is None:
            self.pipeline_name = str(self.id)
        return self


class LivePipelineResult(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    job_id: UUID = Field(foreign_key="job.id")
    target_id: str
    target_name: str = ""
    pipeline_name: str
    status: str  # green / orange / red
    duration: float = 0.0

    job: Optional[Job] = Relationship(back_populates="results")
    steps: list["LiveStepResult"] = Relationship(
        back_populates="pipeline_result",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class LiveStepResult(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    pipeline_result_id: UUID = Field(foreign_key="livepipelineresult.id")
    step_id: str
    success: bool
    stdout: str
    stderr: str
    tried_fix: bool
    skipped: bool
    duration: float = 0.0

    pipeline_result: Optional[LivePipelineResult] = Relationship(back_populates="steps")


class ArchivedRun(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    pipeline_name: str
    target_id: str
    ran_at: datetime
    status: str  # green / orange / red
    changed: bool
    duration: float = 0.0

    steps: list["ArchivedStepResult"] = Relationship(
        back_populates="run",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class ArchivedStepResult(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    archived_run_id: UUID = Field(foreign_key="archivedrun.id")
    step_id: str
    success: bool
    stdout: str
    stderr: str
    tried_fix: bool
    skipped: bool
    duration: float = 0.0

    run: Optional[ArchivedRun] = Relationship(back_populates="steps")


engine = create_engine(f"sqlite:///{config.DB_FILE}")


def init_db():
    SQLModel.metadata.create_all(engine)
    # Add source column to existing databases that predate this field.
    with engine.connect() as conn:
        job_cols = [c["name"] for c in sa_inspect(engine).get_columns("job")]
        if "source" not in job_cols:
            conn.execute(text("ALTER TABLE job ADD COLUMN source VARCHAR NOT NULL DEFAULT 'manual'"))
            conn.commit()

        lpr_cols = [c["name"] for c in sa_inspect(engine).get_columns("livepipelineresult")]
        if "target_name" not in lpr_cols:
            conn.execute(text("ALTER TABLE livepipelineresult ADD COLUMN target_name VARCHAR NOT NULL DEFAULT ''"))
            conn.commit()
        if "duration" not in lpr_cols:
            conn.execute(text("ALTER TABLE livepipelineresult ADD COLUMN duration FLOAT NOT NULL DEFAULT 0.0"))
            conn.commit()

        ar_cols = [c["name"] for c in sa_inspect(engine).get_columns("archivedrun")]
        if "duration" not in ar_cols:
            conn.execute(text("ALTER TABLE archivedrun ADD COLUMN duration FLOAT NOT NULL DEFAULT 0.0"))
            conn.commit()

        lsr_cols = [c["name"] for c in sa_inspect(engine).get_columns("livestepresult")]
        if "duration" not in lsr_cols:
            conn.execute(text("ALTER TABLE livestepresult ADD COLUMN duration FLOAT NOT NULL DEFAULT 0.0"))
            conn.commit()

        asr_cols = [c["name"] for c in sa_inspect(engine).get_columns("archivedstepresult")]
        if "duration" not in asr_cols:
            conn.execute(text("ALTER TABLE archivedstepresult ADD COLUMN duration FLOAT NOT NULL DEFAULT 0.0"))
            conn.commit()
