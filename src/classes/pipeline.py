from typing import Annotated

from pydantic import BaseModel, field_validator, model_validator, Field
from src.classes.connectors import Manager
from src.classes.enums import ExecMethod, CheckMethod, RunnerType, Status


class StepRequirement(BaseModel):
    step: str
    branch: int


class BranchConfig(BaseModel):
    name: str = ""
    signal: Status = Status.ok


class PipelineStep(BaseModel):
    id: str
    exec: str
    exec_method: ExecMethod = ExecMethod.command
    check_method: CheckMethod
    check_patterns: list[str | float | int] | None = None
    branches: list[BranchConfig] = []
    requires: list[StepRequirement] = []

    def get_branch_signal(self, branch: int) -> Status:
        if branch < len(self.branches):
            return self.branches[branch].signal
        if self.check_patterns is None:
            return Status.fail if branch == 1 else Status.ok
        return Status.fail if branch == len(self.check_patterns) else Status.ok

    @model_validator(mode="after")
    def validate_exec_script(self) -> "PipelineStep":
        if self.exec_method == ExecMethod.script:
            from src.config import SCRIPTS_FOLDER
            script_path = SCRIPTS_FOLDER / self.exec
            if not script_path.exists():
                raise ValueError(
                    f"Step '{self.id}': script not found at '{script_path}'"
                )
        return self

    @model_validator(mode="after")
    def validate_check_pattern(self) -> "PipelineStep":
        if self.check_method.requires_pattern() and self.check_patterns is None:
            raise ValueError(
                f"Step '{self.id}': check_pattern is required for check_method '{self.check_method}'"
            )
        if not self.check_method.requires_pattern() and self.check_patterns is not None:
            raise ValueError(
                f"Step '{self.id}': check_pattern is forbidden for check_method '{self.check_method}'"
            )
        if any(req.step == self.id for req in self.requires):
            raise ValueError(
                "Step own id cannot be in requires."
            )

        if self.check_method == CheckMethod.finish_in_less_than:
            self.check_patterns = [float(x) for x in self.check_patterns]

        return self

    @field_validator("id", "exec")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must be a non-empty string")
        return v


class Pipeline(BaseModel):
    name: str
    pipeline: Annotated[list[PipelineStep], Field(min_length=1)]
    connectors: list[str] = []
    runner: RunnerType
    cron: str

    @model_validator(mode="after")
    def validate_requires(self) -> "Pipeline":
        """
        Check that no step requires a non-existing step or a fail-signal branch.
        """
        step_map = {step.id: step for step in self.pipeline}
        for step in self.pipeline:
            unknown = {req.step for req in step.requires} - step_map.keys()
            if unknown:
                raise ValueError(
                    f"Step '{step.id}' in pipeline '{self.name}' requires unknown step(s): {unknown}"
                )
            for req in step.requires:
                if req.step in step_map:
                    sig = step_map[req.step].get_branch_signal(req.branch)
                    if sig == Status.fail:
                        raise ValueError(
                            f"Step '{step.id}' requires step '{req.step}' on branch {req.branch}, "
                            f"which has status 'fail' — only non-fail branches can be required"
                        )
        return self

    @model_validator(mode="after")
    def validate_connectors(self) -> "Pipeline":
        """Check that all referenced connectors exist in the current config."""
        manager = Manager(autoload=True)
        for conn in self.connectors:
            if conn not in manager:
                raise ValueError(f"Connector {conn} not present in the current config.")
        return self

    @field_validator("name")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Pipeline name must be a non-empty string.")
        return v

    @field_validator("cron")
    @classmethod
    def validate_cron(cls, v: str) -> str:
        from apscheduler.triggers.cron import CronTrigger
        try:
            CronTrigger.from_crontab(v)
        except ValueError as e:
            raise ValueError(f"Invalid cron expression: {e}")
        return v

    @model_validator(mode="after")
    def unique_ids(self) -> "Pipeline":
        ids = [step.id for step in self.pipeline]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"Duplicate steps ids in pipeline '{self.name}':", dupes)
        return self
