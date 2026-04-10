from enum import Enum
from typing import Annotated

from pydantic import BaseModel, field_validator, model_validator, Field

from src.classes.connectors import Manager
from src.classes import runner

class CheckMethod(str, Enum):
    exit_code = "exit_code"
    stdout_regex = "stdout_regex"
    stderr_empty = "stderr_empty"
    stdout_contains = "stdout_contains"
    stdout_not_empty = "stdout_not_empty"
    finish_in_less_than = "finish_in_less_than"

    def requires_pattern(self):
        return self in (CheckMethod.stdout_contains, CheckMethod.stdout_regex, CheckMethod.finish_in_less_than)

class PipelineStep(BaseModel):
    id: str
    exec: str
    check_method: CheckMethod
    check_pattern: str | float | int | None = None
    if_failed: str | None
    requires: list[str] = []

    @model_validator(mode="after")
    def validate_check_pattern(self) -> PipelineStep:
        if self.check_method.requires_pattern() and self.check_pattern is None:
            raise ValueError(
                f"Step '{self.id}': check_pattern is required for check_method '{self.check_method}'"
            )
        if not self.check_method.requires_pattern() and self.check_pattern is not None:
            raise ValueError(
                f"Step '{self.id}': check_pattern is forbidden for check_method '{self.check_method}'"
            )
        if self.id in self.requires:
            raise ValueError(
                f"Step own id cannot be in requires."
            )

        if self.check_method == CheckMethod.finish_in_less_than:
            self.check_pattern = float(self.check_pattern)

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
    runner: runner.RunnerType
    cron: str

    @model_validator(mode="after")
    def validate_requires(self) -> Pipeline:
        """
        Check that no steps requires a non-existing step
        :return:
        """
        ids = {step.id for step in self.pipeline}
        for step in self.pipeline:
            unknown = set(step.requires) - ids
            if unknown:
                raise ValueError(
                    f"Step '{step.id}' in pipeline '{self.name}' requires unknown step(s): {unknown}"
                )
        return self

    @model_validator(mode="after")
    def validate_connectors(self) -> Pipeline:
        """
        Check that connectors exist
        :return:
        """
        manager = Manager(autoload=True)
        for conn in self.connectors:
            if conn not in manager:
                raise ValueError(f"Connector {conn} not present in the current config.")
        # No connectors is fine as long as it's in the setup process
        # if not self.connectors:
        #     raise ValueError("A pipeline must have at least one connector")
        return self

    @field_validator("name")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Pipeline name must be a non-empty string.")
        return v

    @model_validator(mode="after")
    def unique_ids(self) -> Pipeline:
        ids = [step.id for step in self.pipeline]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"Duplicate steps ids in pipeline '{self.name}':", dupes)
        return self