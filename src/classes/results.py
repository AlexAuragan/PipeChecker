from dataclasses import dataclass, field

from src.classes.enums import Status
from src.classes.target import Target

_SEVERITY: dict[Status, int] = {
    Status.ok: 0,
    Status.update: 1,
    Status.warning: 2,
    Status.fail: 3,
    Status.crashed: 4,
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
    # (step_id, branch_num) pairs that are depended on by another step — their signal is NOT terminal
    non_leaf_branches: frozenset[tuple[str, int]] = field(default_factory=frozenset)

    @property
    def status(self) -> Status:
        # A step's signal is terminal if the branch it took is not a non-leaf branch
        terminal_signals = [
            s.signal for s in self.steps.values()
            if not s.skipped and (s.step_id, s.branch) not in self.non_leaf_branches
        ]
        if not terminal_signals:
            return Status.ok
        return max(terminal_signals, key=lambda s: _SEVERITY[s])
