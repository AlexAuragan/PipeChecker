import re
import time
from abc import abstractmethod, ABC
from graphlib import TopologicalSorter
from pathlib import Path
from typing import override
import src.classes.target as t
import src.classes.pipeline as p
import src.classes.results as r
from src.classes import utils
from src.classes.enums import ExecMethod, CheckMethod, Status


class Runner(ABC):
    def __init__(self, target: t.Target, pipeline: p.Pipeline):
        self._target = target
        self._pipeline = pipeline

    @property
    def execution_graph(self) -> dict[str, set[str]]:
        return {step.id: {req.step for req in step.requires} for step in self.pipeline.pipeline}

    @property
    def target(self) -> t.Target:
        return self._target

    @property
    def pipeline(self) -> p.Pipeline:
        return self._pipeline

    @abstractmethod
    def run_pipeline(self) -> r.PipelineResult:
        pass

    def _skip_step(self, step: p.PipelineStep) -> r.StepResult:
        return r.StepResult(
            target_id=self.target.id, step_id=step.id,
            signal=Status.ok, stdout="", stderr="",
            branch=-1, skipped=True, duration=0,
        )


class RemoteLinuxRunner(Runner, ABC):
    """Base runner for any target that executes standard Linux commands over SSH."""

    @abstractmethod
    def _exec_command(self, command: str) -> tuple[str, str, int, float]:
        """Execute a shell command. Returns (stdout, stderr, exit_code, duration)."""
        pass

    @abstractmethod
    def _exec_script(self, script_path: Path) -> tuple[str, str, int, float]:
        """Upload and execute a script. Returns (stdout, stderr, exit_code, duration)."""
        pass

    def _run_check(self, step: p.PipelineStep) -> tuple[str, str, int, float]:
        """Run the step command and return (stdout, stderr, branch, duration)."""
        match step.exec_method:
            case ExecMethod.command:
                stdout, stderr, exit_code, duration = self._exec_command(step.exec)
            case ExecMethod.script:
                from src.config import SCRIPTS_FOLDER
                stdout, stderr, exit_code, duration = self._exec_script(SCRIPTS_FOLDER / step.exec)
            case _:
                raise ValueError(f"Unrecognized {step.exec_method}")

        if step.check_patterns is None:
            # Binary check: branch 0 = success, branch 1 = failure
            match step.check_method:
                case CheckMethod.exit_code:
                    branch = 0 if not exit_code else 1
                case CheckMethod.stderr_empty:
                    branch = 0 if not stderr else 1
                case CheckMethod.stdout_not_empty:
                    branch = 0 if stdout else 1
                case _:
                    raise ValueError(f"{step.check_method} not recognized as a binary CheckMethod")
        else:
            # Pattern-based: branch i = patterns[i] matched, branch len(patterns) = no match
            branch = len(step.check_patterns)
            for i, pattern in enumerate(step.check_patterns):
                match step.check_method:
                    case CheckMethod.stdout_contains:
                        if str(pattern) in stdout:
                            branch = i
                            break
                    case CheckMethod.stdout_regex:
                        if re.findall(str(pattern), stdout):
                            branch = i
                            break
                    case CheckMethod.finish_in_less_than:
                        if duration < float(pattern):
                            branch = i
                            break
                    case _:
                        raise ValueError(f"{step.check_method} not recognized as a pattern CheckMethod")

        return stdout, stderr, branch, duration

    def _run_step(self, step: p.PipelineStep) -> r.StepResult:
        stdout, stderr, branch, duration = self._run_check(step)
        signal = step.get_branch_signal(branch)
        return r.StepResult(
            self.target.id, step.id,
            signal=signal, stdout=stdout, stderr=stderr,
            branch=branch, skipped=False, duration=duration,
        )

    @override
    def run_pipeline(self) -> r.PipelineResult:
        import traceback
        steps_by_id = {step.id: step for step in self.pipeline.pipeline}
        non_leaf_branches = frozenset(
            (req.step, req.branch)
            for step in self.pipeline.pipeline
            for req in step.requires
        )

        sorter = TopologicalSorter(self.execution_graph)
        sorter.prepare()

        results_by_id: dict[str, r.StepResult] = {}
        start = time.time()
        while sorter.is_active():
            for step_id in sorter.get_ready():
                step = steps_by_id[step_id]
                should_skip = any(
                    results_by_id[req.step].branch != req.branch
                    for req in step.requires
                    if req.step in results_by_id
                )
                if should_skip:
                    res = self._skip_step(step)
                else:
                    try:
                        res = self._run_step(step)
                    except Exception:
                        res = r.StepResult(
                            target_id=self.target.id, step_id=step_id,
                            signal=Status.crashed, stdout="", stderr=traceback.format_exc(),
                            branch=-1, skipped=False, duration=0,
                        )
                results_by_id[step_id] = res
                sorter.done(step_id)
        pipes_results = {step.id: results_by_id[step.id] for step in self.pipeline.pipeline}
        end = time.time()
        return r.PipelineResult(
            target=self.target,
            pipeline_name=self.pipeline.name,
            steps=pipes_results,
            duration=end - start,
            non_leaf_branches=non_leaf_branches,
        )


class PCTRunner(RemoteLinuxRunner):
    @override
    @property
    def target(self) -> t.ProxmoxCT:
        return self._target  # type:ignore

    @override
    def __init__(self, target: t.ProxmoxCT, pipeline: p.Pipeline):
        super().__init__(target, pipeline)

    def _exec_command(self, command: str) -> tuple[str, str, int, float]:
        return utils.execute_on_ct(self.target, command)

    def _exec_script(self, script_path: Path) -> tuple[str, str, int, float]:
        return utils.execute_script_on_ct(self.target, script_path)


class LinuxMachineRunner(RemoteLinuxRunner):
    @override
    @property
    def target(self) -> t.RemoteLinuxMachine:
        return self._target  # type:ignore

    @override
    def __init__(self, target: t.RemoteLinuxMachine, pipeline: p.Pipeline):
        super().__init__(target, pipeline)

    def _exec_command(self, command: str) -> tuple[str, str, int, float]:
        return utils.execute_on_linux(self.target, command)

    def _exec_script(self, script_path: Path) -> tuple[str, str, int, float]:
        return utils.execute_script_on_linux(self.target, script_path)
