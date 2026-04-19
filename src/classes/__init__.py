import src.classes.connectors as _connectors
from src.classes.connectors import ConnectorType
from src.classes.enums import RunnerType
from src.classes.runner import LinuxMachineRunner, PCTRunner, Runner, RemoteLinuxRunner

connectors: dict[str, type[_connectors.Connector]] = {
    "Proxmox": _connectors.Proxmox,
    "Caddy": _connectors.Caddy,
    "Linux Remote Machine": _connectors.LinuxMachine,
}

CONNECTOR_RUNNER_MAP: dict[ConnectorType, RunnerType] = {
    ConnectorType.proxmox: RunnerType.proxmox_ct,
    ConnectorType.linux_machine: RunnerType.linux_machine,
    ConnectorType.caddy: RunnerType.web
}


__all__ = [
    "connectors",
    "ConnectorType",
    "CONNECTOR_RUNNER_MAP",
    "RunnerType",
    "Runner",
    "RemoteLinuxRunner",
    "LinuxMachineRunner",
    "PCTRunner"
]
