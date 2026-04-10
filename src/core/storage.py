import os
from pathlib import Path

import yaml

from src import config
from src.classes import pipeline
from src.classes.connectors import Manager, Connector


def load_pipeline_config(path: str | Path) -> tuple[list[str],dict[str, pipeline.Pipeline]]:
    raw = yaml.safe_load(Path(path).read_text())
    pipes_raw = raw["pipelines"]
    connectors = raw["connectors"]
    if not isinstance(pipes_raw, list):
        raise ValueError("Config file must be a YAML list at the `pipelines` level")
    pipes = []
    for pipe in pipes_raw:
        pipe["connectors"] = connectors
        pipes.append(pipeline.Pipeline.model_validate(pipe))
    return connectors, {p.name: p for p in pipes}

def load_pipelines(group: str = None) -> dict[str,dict[str, pipeline.Pipeline]]:
    out = {}
    if group and "." in group:
        raise ValueError("Forbidden character in group name")
    seen = set()
    for conf in os.listdir(config.PIPELINE_FOLDER):
        if group and group != conf.split(".")[0]:
            continue
        pipes = load_pipeline_config(config.PIPELINE_FOLDER / conf)[1]
        if any(k in seen for k in pipes.keys()):
            # TODO
            raise ValueError("Two pipelines with the same name found in different group. Still brainstorming about what to do in that case")
        seen.update(pipes.keys())
        out[conf.split(".")[0]] = pipes
    return out

def save_pipeline(pipe: pipeline.Pipeline, group: str) -> None:
    group = group.split(".")[0]
    path = config.PIPELINE_FOLDER / f"{group}.yaml"
    if path.exists():
        raw = yaml.safe_load(path.read_text())
        raw.setdefault("pipelines", [])
        raw.setdefault("connectors", [])
    else:
        raw = {"connectors": pipe.connectors, "pipelines": []}
    pipe_dict = {
        "name": pipe.name,
        "runner": pipe.runner.value,
        "cron": pipe.cron,
        "pipeline": [s.model_dump(mode="json") for s in pipe.pipeline],
    }
    raw["pipelines"].append(pipe_dict)
    with open(path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)

def update_pipeline(pipe: pipeline.Pipeline, group: str) -> None:
    group = group.split(".")[0]
    path = config.PIPELINE_FOLDER / f"{group}.yaml"
    raw = yaml.safe_load(path.read_text())
    pipe_dict = pipe.model_dump(mode="json", exclude={"connectors"})
    raw["pipelines"] = [
      pipe_dict if p["name"] == pipe.name else p
      for p in raw["pipelines"]
    ]
    with open(path, "w") as f:
      yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)

def delete_pipeline(name: str, group: str) -> None:
  group = group.split(".")[0]
  path = config.PIPELINE_FOLDER / f"{group}.yaml"
  raw = yaml.safe_load(path.read_text())
  raw["pipelines"] = [p for p in raw["pipelines"] if p["name"] != name]
  if not raw["pipelines"]:
      path.unlink()
  else:
      with open(path, "w") as f:
          yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)

def load_manager() -> Manager:
    manager = Manager()
    with open(config.CONNECTOR_FILE) as f:
        data = yaml.safe_load(f)
    if data is None:
        return manager
    for name, conf in data.items():
        # reconstruct the per-connector yaml string and reuse from_str
        connector = Connector.from_str(yaml.dump({name: conf}))
        manager.add(connector)
    return manager

def save_manager(manager: Manager) -> None:
    data = {}
    for conn in manager:
        data |= yaml.safe_load(conn.to_str())
    with open(config.CONNECTOR_FILE, "w") as f:
        yaml.dump(data, f, default_flow_style=False)

