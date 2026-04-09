from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import model_validator
from sqlmodel import SQLModel, Field, Relationship, create_engine

from src import config


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Job(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    pipeline_name: str | None = None
    status: JobStatus = JobStatus.pending
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
    pipeline_name: str
    status: str  # green / orange / red

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

    pipeline_result: Optional[LivePipelineResult] = Relationship(back_populates="steps")


class ArchivedRun(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    pipeline_name: str
    target_id: str
    ran_at: datetime
    status: str  # green / orange / red
    changed: bool

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

    run: Optional[ArchivedRun] = Relationship(back_populates="steps")


engine = create_engine(f"sqlite:///{config.DB_FILE}")


def init_db():
    SQLModel.metadata.create_all(engine)
