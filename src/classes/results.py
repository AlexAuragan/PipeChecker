from dataclasses import dataclass
from typing import Literal

from src.classes.enums import Status
from src.classes.target import Target


@dataclass
class StepResult:
    target_id: str
    step_id: str
    signal: Status
    stdout: str
    stderr: str
    branch: int
    skipped: bool
    duration: float


@dataclass
class PipelineResult:
    target: Target
    pipeline_name: str
    steps: dict[str, "StepResult"]
    duration: float

    @property
    def status(self) -> Literal["green", "orange", "red"]:
        signals = [s.signal for s in self.steps.values() if not s.skipped]
        if not signals:
            return "green"
        if any(s == Status.fail for s in signals):
            return "red"
        if any(s in (Status.warning, Status.update) for s in signals):
            return "orange"
        return "green"
