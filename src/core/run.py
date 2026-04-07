from collections.abc import Callable

from src.classes.connectors import Manager
from src.classes.pipeline import Pipeline
from src.classes.runner import Runner, PCTRunner, RunnerType
from src.classes.target import Target, ProxmoxCT
from src.classes.results import PipelineResult


def get_runner(pipeline: Pipeline, target: Target) -> Runner:
    match pipeline.runner:
        case RunnerType.proxmox_ct:
            assert isinstance(target, ProxmoxCT)
            return PCTRunner(target, pipeline)
        case _:
            raise NotImplementedError("Not implemented yet")


def run_pipeline(
    pipeline: Pipeline,
    manager: Manager,
    on_result: Callable[[PipelineResult], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[PipelineResult]:
    results = []
    for connector_name in pipeline.connectors:
        connector = manager.get(connector_name)
        for target in connector.targets:
            if should_stop and should_stop():
                return results
            runner = get_runner(pipeline, target)
            result = runner.run_pipeline()
            results.append(result)
            if on_result:
                on_result(result)
    return results