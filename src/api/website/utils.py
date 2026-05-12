import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from starlette.datastructures import FormData
from starlette.templating import Jinja2Templates

from src.classes import CONNECTOR_RUNNER_MAP, connectors
from src.classes.connectors import Connector, ConnectorType
from src.classes.pipeline import CheckMethod, Pipeline, PipelineStep
from src.config import ALLOWED_SCRIPT_EXTENSIONS, SCRIPTS_FOLDER
from src.core import storage

## Script helper


def list_scripts() -> list[str]:
    """Return all .sh and .py scripts under SCRIPTS_FOLDER, sorted, as relative path strings."""
    if not SCRIPTS_FOLDER.exists():
        return []
    return sorted(
        str(p.relative_to(SCRIPTS_FOLDER))
        for p in SCRIPTS_FOLDER.rglob("*")
        if p.is_file() and p.suffix in ALLOWED_SCRIPT_EXTENSIONS
    )


## Form helpers


def form_base_ctx() -> dict[str, Any]:
    return {
        "check_methods": list(CheckMethod),
        "available_scripts": list_scripts(),
    }


def available_connectors() -> list[dict[str, Any]]:
    return [{"name": c.name, "runner_type": CONNECTOR_RUNNER_MAP[c.type].value} for c in storage.load_manager()]


def get_step_branches(step: dict[str, Any] | PipelineStep | None) -> list[dict[str, Any]]:
    """Return [{index, name, signal}] for every branch of a step (dict or model)."""
    if step is None:
        return [
            {"index": 0, "name": "pass", "signal": "ok"},
            {"index": 1, "name": "fail", "signal": "fail"},
        ]
    if isinstance(step, dict):
        step = PipelineStep.model_validate(step)

    patterns = step.check_patterns or None
    raw_branches = step.branches

    def _name(i: int, default: str) -> str:
        if i < len(raw_branches):
            return raw_branches[i].name.strip() or default
        return default

    def _signal(i: int, default: str) -> str:
        if i < len(raw_branches):
            return raw_branches[i].signal.value
        return default

    if patterns is None:
        return [
            {"index": 0, "name": _name(0, "pass"), "signal": _signal(0, "ok")},
            {"index": 1, "name": _name(1, "fail"), "signal": _signal(1, "fail")},
        ]

    branches = [
        {"index": i, "name": _name(i, str(patterns[i])), "signal": _signal(i, "ok")} for i in range(len(patterns))
    ]
    branches.append(
        {
            "index": len(patterns),
            "name": _name(len(patterns), "no match"),
            "signal": _signal(len(patterns), "fail"),
        }
    )
    return branches


def _fget(form: FormData, key: str, default: str = "") -> str:
    v = form.get(key)
    return v if isinstance(v, str) else default


def _parse_requires_entry(v: str) -> dict[str, Any]:
    if ":" in v:
        step_id, branch = v.rsplit(":", 1)
        try:
            return {"step": step_id, "branch": int(branch)}
        except ValueError:
            pass
    return {"step": v, "branch": 0}


def _parse_check_patterns(form: FormData, i: int) -> list[str] | None:
    patterns = [
        v.strip() for k, v in form.multi_items() if k == f"step_check_patterns_{i}" and isinstance(v, str) and v.strip()
    ]
    return patterns if patterns else None


def _parse_branches(form: FormData, i: int, patterns: list[str] | None) -> list[dict[str, Any]]:
    names = [v.strip() for k, v in form.multi_items() if k == f"step_branch_names_{i}" and isinstance(v, str)]
    signals = [v.strip() for k, v in form.multi_items() if k == f"step_branch_signals_{i}" and isinstance(v, str)]
    pairs = list(zip(names, signals))
    # The hidden branch section (binary or pattern) always submits its inputs too.
    # For pattern steps: binary inputs land first (2 pairs), real data follows.
    # For binary steps: at most 2 pairs are valid.
    if patterns is not None:
        expected = len(patterns) + 1  # one per pattern + no-match
        if len(pairs) > expected:
            pairs = pairs[len(pairs) - expected :]
    else:
        pairs = pairs[:2]
    return [{"name": n, "signal": s or "ok"} for n, s in pairs]


def steps_from_form(form: FormData) -> list[tuple[int, dict[str, Any]]]:
    """Re-inflate step rows from raw POST form data (for error re-render)."""
    indices = sorted({int(k[len("step_id_") :]) for k in form.keys() if k.startswith("step_id_")})
    rows = []
    for i in indices:
        exec_method = form.get(f"step_exec_method_{i}", "command")
        exec_command = form.get(f"step_exec_command_{i}", "")
        exec_script = form.get(f"step_exec_script_{i}", "")
        requires_raw = [v for k, v in form.multi_items() if k == f"step_requires_{i}" and isinstance(v, str)]
        rows.append(
            (
                i,
                {
                    "id": form.get(f"step_id_{i}", ""),
                    "exec_method": exec_method,
                    "exec_command": exec_command,
                    "exec_script": exec_script,
                    "exec": exec_command if exec_method == "command" else exec_script,
                    "check_method": form.get(f"step_check_method_{i}", "exit_code"),
                    "check_patterns": _parse_check_patterns(form, i) or [],
                    "branches": _parse_branches(form, i, _parse_check_patterns(form, i)),
                    "requires": [_parse_requires_entry(r) for r in requires_raw],
                },
            )
        )
    return rows


def step_ids_from_form(form: FormData) -> list[str]:
    indices = sorted({int(k[len("step_id_") :]) for k in form.keys() if k.startswith("step_id_")})
    return [sid for i in indices if (sid := _fget(form, f"step_id_{i}").strip())]


def parse_pipeline_form(form: FormData) -> tuple[str, Pipeline]:
    group = _fget(form, "group", "default").strip() or "default"
    name = _fget(form, "name").strip()
    cron = _fget(form, "cron").strip()
    runner_val = _fget(form, "runner").strip()
    connector_list = [v for k, v in form.multi_items() if k == "connectors" and isinstance(v, str)]

    indices = sorted({int(k[len("step_id_") :]) for k in form.keys() if k.startswith("step_id_")})
    steps = []
    for i in indices:
        sid = _fget(form, f"step_id_{i}").strip()
        if not sid:
            continue
        exec_method = (_fget(form, f"step_exec_method_{i}") or "command").strip()
        if exec_method == "script":
            exec_val = _fget(form, f"step_exec_script_{i}").strip()
        else:
            exec_val = _fget(form, f"step_exec_command_{i}").strip()
        requires_raw = [v for k, v in form.multi_items() if k == f"step_requires_{i}" and isinstance(v, str)]
        steps.append(
            {
                "id": sid,
                "exec": exec_val,
                "exec_method": exec_method,
                "check_method": form.get(f"step_check_method_{i}") or "exit_code",
                "check_patterns": (patterns := _parse_check_patterns(form, i)),
                "branches": _parse_branches(form, i, patterns),
                "requires": [_parse_requires_entry(r) for r in requires_raw],
            }
        )
    return group, Pipeline.model_validate(
        {
            "name": name,
            "cron": cron,
            "runner": runner_val,
            "connectors": connector_list,
            "pipeline": steps,
        }
    )


def parse_connector_form(form: FormData) -> Connector:
    name = _fget(form, "name").strip()
    type_val = _fget(form, "type").strip()
    config_ssh = [v.strip() for k, v in form.multi_items() if k == "config_ssh" and isinstance(v, str) and v.strip()]
    config_url = [v.strip() for k, v in form.multi_items() if k == "config_url" and isinstance(v, str) and v.strip()]
    config_path = [v.strip() for k, v in form.multi_items() if k == "config_path" and isinstance(v, str) and v.strip()]
    connector_type = ConnectorType(type_val)
    cls = connectors[connector_type.value]
    return cls.model_validate(
        {
            "name": name,
            "config_ssh": config_ssh,
            "config_url": config_url,
            "config_path": config_path,
        }
    )


def connector_form_data(form: FormData, name_override: str | None = None) -> dict[str, Any]:
    return {
        "name": name_override or _fget(form, "name"),
        "type": _fget(form, "type", ConnectorType.proxmox.value),
        "config_ssh": [v for k, v in form.multi_items() if k == "config_ssh" and isinstance(v, str)],
        "config_path": [v for k, v in form.multi_items() if k == "config_path" and isinstance(v, str)],
        "config_url": [v for k, v in form.multi_items() if k == "config_url" and isinstance(v, str)],
    }


## Other helpers


def compute_columns(steps: list[PipelineStep]) -> list[list[PipelineStep]]:
    """Assign each step to a column by longest-path depth in the dependency graph."""
    step_map = {s.id: s for s in steps}
    depths: dict[str, int] = {}

    def depth(sid: str) -> int:
        if sid in depths:
            return depths[sid]
        step = step_map[sid]
        depths[sid] = 0 if not step.requires else max(depth(req.step) for req in step.requires) + 1
        return depths[sid]

    for s in steps:
        depth(s.id)

    num_cols = max(depths.values()) + 1 if depths else 1
    columns: list[list[Any]] = [[] for _ in range(num_cols)]
    for s in steps:
        columns[depths[s.id]].append(s)
    return columns


def build_edges(steps: list[PipelineStep]) -> str:
    edges = [{"from": req.step, "branch": req.branch, "to": step.id} for step in steps for req in step.requires]
    return json.dumps(edges)


_SIGNAL_GROUP = {
    "skipped": "gray",
    "ok": "green",
    "update": "orange",
    "installed": "orange",
    "warning": "orange",
    "fail": "red",
    "crashed": "red",
}


def signal_group(signal: Any) -> str:
    """Map a pipeline signal to a color group (green/orange/red) for filtering."""
    s = signal.value if hasattr(signal, "value") else str(signal)
    return _SIGNAL_GROUP.get(s, "green")


def status_badge(status: Any) -> str:
    """Map a job/target status value to a CSS badge class."""
    s = status.value if hasattr(status, "value") else str(status)
    return {
        "completed": "badge-green",
        "failed": "badge-red",
        "running": "badge-blue",
        "pending": "badge-gray",
        "cancelled": "badge-gray",
        "skipped": "badge-gray",
        "ok": "badge-green",
        "update": "badge-blue",
        "installed": "badge-purple",
        "warning": "badge-orange",
        "fail": "badge-red",
        "crashed": "badge-crashed",
    }.get(s, "badge-gray")


# Maps Status enum values to CSS utility classes used in templates.
_SIGNAL_CLASS = {
    "skipped": "status-skipped",
    "ok": "status-green",
    "update": "status-running",
    "installed": "status-installed",
    "warning": "status-orange",
    "fail": "status-red",
    "crashed": "status-red",
}
_SIGNAL_BADGE = {
    "skipped": "badge-gray",
    "ok": "badge-green",
    "update": "badge-blue",
    "installed": "badge-purple",
    "warning": "badge-orange",
    "fail": "badge-red",
    "crashed": "badge-crashed",
}


def step_class(step_result: dict[str, Any]) -> str:
    if step_result["skipped"]:
        return "status-skipped"
    return _SIGNAL_CLASS.get(step_result.get("signal", "ok"), "status-red")


def step_badge(step_result: dict[str, Any]) -> str:
    if step_result["skipped"]:
        return "badge-gray"
    return _SIGNAL_BADGE.get(step_result.get("signal", "ok"), "badge-red")


def step_text(step_result: dict[str, Any]) -> str:
    if step_result["skipped"]:
        return "skipped"
    signal = step_result.get("signal", "ok")
    if signal != "ok":
        return signal
    branch = step_result["branch"]
    return "pass" if branch == 0 else f"branch {branch}"


def source_badge(source: Any) -> str:
    s = source.value if hasattr(source, "value") else str(source)
    return {"manual": "badge-gray", "cron": "badge-blue", "event": "badge-orange"}.get(s, "badge-gray")


def signal_badge(signal: Any) -> str:
    """Map a signal string or Status enum → CSS badge class."""
    s = signal.value if hasattr(signal, "value") else str(signal)
    return _SIGNAL_BADGE.get(s, "badge-gray")


def fmt_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M")


def fmt_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


## Template

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

templates.env.filters["tojson"] = lambda v: json.dumps(v)
cast(dict[str, Any], templates.env.globals).update(
    status_badge=status_badge,
    signal_group=signal_group,
    signal_badge=signal_badge,
    source_badge=source_badge,
    step_class=step_class,
    step_badge=step_badge,
    step_text=step_text,
    fmt_duration=fmt_duration,
    fmt_datetime=fmt_datetime,
    get_step_branches=get_step_branches,
)
