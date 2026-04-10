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
        all_success = all(s.success for s in self.steps.values() if not s.skipped)
        if all_success:
            return "orange" if any(s.tried_fix for s in self.steps.values()) else "green"
        return "red"