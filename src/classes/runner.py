import re
import time
from abc import abstractmethod, ABC
from enum import Enum
from graphlib import TopologicalSorter
from typing import override
from src.classes import target as t, pipeline as p, results as r, utils

class RunnerType(str, Enum):
    proxmox_ct = "proxmox_ct"
    # TODO
    # machine = "machine"
    # web = "web"


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

class PCTRunner(Runner):

    @override
    @property
    def target(self) -> t.ProxmoxCT:
        return self._target # type:ignore

    @override
    def __init__(self, target: t.ProxmoxCT, pipeline: p.Pipeline):
        super().__init__(target, pipeline)

    def _run_check(self, step: p.PipelineStep, if_failed: bool = False) -> tuple[str, str, bool, float]:
        if if_failed:
            # if_failed is always a plain command regardless of exec_method
            stdout, stderr, exit_code, duration = utils.execute_on_ct(self.target, step.if_failed)
        else:
            match step.exec_method:
                case p.ExecMethod.command:
                    stdout, stderr, exit_code, duration = utils.execute_on_ct(self.target, step.exec)
                case p.ExecMethod.script:
                    from src.config import SCRIPTS_FOLDER
                    stdout, stderr, exit_code, duration = utils.execute_script_on_ct(self.target, SCRIPTS_FOLDER / step.exec)


        success = None
        match step.check_method:
            case p.CheckMethod.exit_code:
                success = not bool(exit_code)
            case p.CheckMethod.stderr_empty:
                success = not bool(stderr)
            case p.CheckMethod.stdout_contains:
                success = step.check_pattern in stdout
            case p.CheckMethod.stdout_regex:
                success = len(re.findall(step.check_pattern, stdout)) >= 1
            case p.CheckMethod.stdout_not_empty:
                success = bool(stdout)
            case p.CheckMethod.finish_in_less_than:
                success = duration < int(step.check_pattern)
            case _:
                raise ValueError(step.check_method, "not recognized as a CheckMethod")
        return stdout, stderr, success, duration

    def _run_step(self, step: p.PipelineStep):
        """
        Execute the exec command for the step, try the fix if it can, return the StepResult associated
        :param step:
        :return:
        """
        stdout, stderr, success, duration = self._run_check(step)
        if success:
            return r.StepResult(self.target.id, step.id, success=True, stdout=stdout, stderr=stderr, tried_fix=False, skipped=False, duration=duration)
        else:
            if step.if_failed:
                print(f"warning, step failed; stdout: {stdout}; stderr: {stderr}; success: {success}; command: {step.exec}")
                stdout, stderr, success, duration_2 = self._run_check(step, if_failed=True)
                duration += duration_2
                return r.StepResult(self.target.id, step.id, success=success, stdout=stdout, stderr=stderr, tried_fix=True, skipped=False, duration=duration)
            return r.StepResult(self.target.id, step.id, success=success, stdout=stdout, stderr=stderr, tried_fix=False, skipped=False, duration=duration)

    def _skip_step(self, step: p.PipelineStep):
        return r.StepResult(target_id=self.target.id, step_id=step.id, success=False, stdout="", stderr="", tried_fix=False, skipped=True, duration=0)


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
        pipes_results = {step.id:results_by_id[step.id] for step in self.pipeline.pipeline}
        end = time.time()
        return r.PipelineResult(
            target=self.target,
            pipeline_name=self.pipeline.name,
            steps=pipes_results,
            duration=end - start
        )