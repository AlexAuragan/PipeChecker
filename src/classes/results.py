from dataclasses import dataclass
from typing import Literal

from src.classes.target import Target


@dataclass
class StepResult:
    target_id: str
    step_id: str
    success: bool
    stdout: str
    stderr: str
    tried_fix: bool
    skipped: bool

@dataclass
class PipelineResult:
    target: Target
    pipeline_name: str
    steps: dict[str, StepResult]

    @property
    def status(self) -> Literal["green", "orange", "red"]:
        if all(s.success for s in self.steps.values() if not s.skipped):
            return "green"
        if any(s.tried_fix for s in self.steps.values()):
            return "orange"
        return "red"