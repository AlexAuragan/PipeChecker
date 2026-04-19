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
from src.classes.enums import ExecMethod, CheckMethod


class Runner(ABC):
    def __init__(self, target: t.Target, pipeline: p.Pipeline):
        self._target = target
        self._pipeline = pipeline

    @property
    def execution_graph(self) -> dict[str, set[str]]:
        return {step.id: set(step.requires) for step in self.pipeline.pipeline}

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
        return r.StepResult(target_id=self.target.id, step_id=step.id, success=False, stdout="", stderr="", tried_fix=False, skipped=True, duration=0)


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

    def _run_check(self, step: p.PipelineStep, if_failed: bool = False) -> tuple[str, str, bool, float]:
        stdout, stderr, exit_code, duration = None, None, None, None
        if if_failed:
            stdout, stderr, exit_code, duration = self._exec_command(step.if_failed)
        else:
            match step.exec_method:
                case ExecMethod.command:
                    stdout, stderr, exit_code, duration = self._exec_command(step.exec)
                case ExecMethod.script:
                    from src.config import SCRIPTS_FOLDER
                    stdout, stderr, exit_code, duration = self._exec_script(SCRIPTS_FOLDER / step.exec)
                case _:
                    raise ValueError(f"Unrecognized {step.exec_method}")

        match step.check_method:
            case CheckMethod.exit_code:
                success = not bool(exit_code)
            case CheckMethod.stderr_empty:
                success = not bool(stderr)
            case CheckMethod.stdout_contains:
                success = step.check_pattern in stdout
            case CheckMethod.stdout_regex:
                success = len(re.findall(step.check_pattern, stdout)) >= 1
            case CheckMethod.stdout_not_empty:
                success = bool(stdout)
            case CheckMethod.finish_in_less_than:
                success = duration < int(step.check_pattern)
            case _:
                raise ValueError(step.check_method, "not recognized as a CheckMethod")
        return stdout, stderr, success, duration

    def _run_step(self, step: p.PipelineStep) -> r.StepResult:
        """Run a step, attempt the fix command on failure, and return the result."""
        stdout, stderr, success, duration = self._run_check(step)
        if success:
            return r.StepResult(self.target.id, step.id, success=True, stdout=stdout, stderr=stderr, tried_fix=False, skipped=False, duration=duration)
        if step.if_failed:
            print(f"warning, step failed; stdout: {stdout}; stderr: {stderr}; success: {success}; command: {step.exec}")
            stdout, stderr, success, duration_2 = self._run_check(step, if_failed=True)
            duration += duration_2
            return r.StepResult(self.target.id, step.id, success=success, stdout=stdout, stderr=stderr, tried_fix=True, skipped=False, duration=duration)
        return r.StepResult(self.target.id, step.id, success=success, stdout=stdout, stderr=stderr, tried_fix=False, skipped=False, duration=duration)

    @override
    def run_pipeline(self) -> r.PipelineResult:
        steps_by_id = {step.id: step for step in self.pipeline.pipeline}
        sorter = TopologicalSorter(self.execution_graph)
        sorter.prepare()

        results_by_id: dict[str, r.StepResult] = {}
        failed: set[str] = set()
        start = time.time()
        while sorter.is_active():
            for step_id in sorter.get_ready():
                step = steps_by_id[step_id]
                if failed & self.execution_graph[step_id]:
                    res = self._skip_step(step)
                    failed.add(step_id)
                else:
                    res = self._run_step(step)
                    if not res.success:
                        failed.add(step_id)
                results_by_id[step_id] = res
                sorter.done(step_id)
        pipes_results = {step.id: results_by_id[step.id] for step in self.pipeline.pipeline}
        end = time.time()
        return r.PipelineResult(
            target=self.target,
            pipeline_name=self.pipeline.name,
            steps=pipes_results,
            duration=end - start
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
