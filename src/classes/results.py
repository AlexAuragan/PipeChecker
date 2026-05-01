from dataclasses import dataclass

from src.classes.enums import Status
from src.classes.target import Target

_SEVERITY: dict[Status, int] = {
    Status.ok: 0,
    Status.update: 1,
    Status.installed: 2,
    Status.warning: 3,
    Status.fail: 4,
    Status.crashed: 5,
}


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
    def status(self) -> Status:
        signals = [s.signal for s in self.steps.values() if not s.skipped]
        if not signals:
            return Status.ok
        return max(signals, key=lambda s: _SEVERITY[s])
