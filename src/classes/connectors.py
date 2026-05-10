from abc import ABC, abstractmethod
from collections.abc import ItemsView, Iterator, KeysView, ValuesView
from ipaddress import IPv4Address
from itertools import zip_longest
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    PrivateAttr,
    field_validator,
    model_validator,
)

from src import config
import src.classes.utils as utils
import src.classes.target as target
from src.classes.enums import ConnectorType
from src.misc.caddy_parser import parse_caddyfile
from src.misc.simple_parsers import parse_table, pct_config_parser


class Manager:
    def __init__(self, autoload: bool = True):
        super().__init__()
        self._connectors: dict[str, Connector] = {}
        if not autoload:
            return
        with open(config.CONNECTOR_FILE) as f:
            data = yaml.safe_load(f)
        if data is None:
            return
        for name, conf in data.items():
            # reconstruct the per-connector yaml string and reuse from_str
            connector = Connector.from_str(yaml.dump({name: conf}))
            self.add(connector)

    def add(self, connector: Connector) -> None:
        self._connectors[connector.name] = connector

    def get(self, name: str) -> Connector:
        return self._connectors[name]

    def remove(self, name: str) -> None:
        del self._connectors[name]

    def __iter__(self) -> Iterator[Connector]:
        return iter(self._connectors.values())

    def __contains__(self, name: str) -> bool:
        return name in self._connectors

    def keys(self) -> KeysView[str]:
        return self._connectors.keys()

    def values(self) -> ValuesView[Connector]:
        return self._connectors.values()

    def items(self) -> ItemsView[str, Connector]:
        return self._connectors.items()

    def load_targets(self) -> None:
        for conn in self:
            try:
                conn.load_targets()
            except Exception as e:
                print(f"Failed to load targets for connector '{conn.name}': {e}")
                conn._targets = []
                conn._load_error = str(e)


class Connector(BaseModel, ABC):
    """
    Used to extract list of Target from various configs
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str
    type: ConnectorType
    config_path: list[str] = []
    config_url: list[str] = []
    config_ssh: list[str] = []
    _targets: list[target.Target] | None = PrivateAttr(default=None)
    _load_error: str | None = PrivateAttr(default=None)

    @field_validator("config_path", "config_url", "config_ssh", mode="before")
    @classmethod
    def coerce_to_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        return [v] if isinstance(v, str) else list(v)

    @model_validator(mode="after")
    def check_exclusivity(self) -> "Connector":
        if self.config_ssh and self.config_url:
            raise ValueError("config_url and config_ssh are exclusive.")
        if self.config_path and self.config_url:
            raise ValueError("config_url and config_path are exclusive.")
        return self

    @abstractmethod
    def single_init(
        self,
        config_path: str | Path | None = None,
        config_url: str | None = None,
        config_ssh: str | None = None,
    ) -> list[target.Target]:
        """Fetch targets from one config source. Exactly one argument will be non-None."""
        pass

    def load_targets(self) -> None:
        self._targets = []
        for cp, cu, cs in zip_longest(self.config_path, self.config_url, self.config_ssh):
            self._targets += self.single_init(config_path=cp, config_url=cu, config_ssh=cs)

    @property
    def is_loaded(self) -> bool:
        return self._targets is not None

    @property
    def targets(self) -> list[target.Target]:
        if self._load_error is not None:
            raise RuntimeError(f"Connector '{self.name}' failed to load targets: {self._load_error}")
        if self._targets is None:
            self.load_targets()
        assert self._targets is not None
        return self._targets

    def to_str(self) -> str:
        data = self.model_dump(mode="json")
        name = data.pop("name")
        data = {k: v for k, v in data.items() if v}
        return yaml.dump({name: data}, default_flow_style=False).strip()

    @staticmethod
    def from_str(content: str) -> "Connector":
        data = yaml.safe_load(content)
        name, conf = next(iter(data.items()))
        cls = _TYPE_MAP[ConnectorType(conf.pop("type")).value]
        return cls.model_validate({"name": name, **conf})


class Caddy(Connector):
    type: Literal[ConnectorType.caddy] = ConnectorType.caddy
    config_path: list[str] = ["/etc/caddy/Caddyfile"]

    def single_init(
        self,
        config_path: str | Path | None = None,
        config_url: str | None = None,
        config_ssh: str | None = None,
    ) -> list[target.Target]:
        content: str
        if config_url:
            content = utils.get_file_from_url(config_url).decode("utf-8")
        elif config_path is None:
            raise ValueError("config_url or config_path must be set")
        else:
            content = utils.get_file_from_path(config_path, config_ssh).decode("utf-8")

        parsed = parse_caddyfile(content)
        return [target.Url(addr) for addr in parsed]


class LinuxMachine(Connector):
    type: Literal[ConnectorType.linux_machine] = ConnectorType.linux_machine
    exec_dir: str = "/"

    @model_validator(mode="before")
    @classmethod
    def ignore_unused_configs(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data["config_path"] = []
            data["config_url"] = []
        return data

    def single_init(
        self,
        config_path: str | Path | None = None,
        config_url: str | None = None,
        config_ssh: str | None = None,
    ) -> list[target.Target]:
        assert config_ssh is not None
        hostname = utils.execute_on_machine(config_ssh, "hostname").strip()
        user, ip = config_ssh.split("@")
        return [
            target.RemoteLinuxMachine(
                machine_ip=IPv4Address(ip),
                user=user,
                exec_dir=self.exec_dir,
                hostname=hostname,
            )
        ]


class Proxmox(Connector):
    type: Literal[ConnectorType.proxmox] = ConnectorType.proxmox

    @model_validator(mode="before")
    @classmethod
    def ignore_unused_configs(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data["config_path"] = []
            data["config_url"] = []
        return data

    def single_init(
        self,
        config_path: str | Path | None = None,
        config_url: str | None = None,
        config_ssh: str | None = None,
    ) -> list[target.Target]:
        assert config_ssh is not None
        stdout = utils.execute_on_machine(config_ssh, "pct list")
        pct_list = parse_table(stdout)
        stdout = utils.execute_on_machine(config_ssh, "hostname")
        hostname = stdout.strip()

        # Grab all the CT config by cat-ing the confing, faster than running pct info N times
        pct_ids = [pct["VMID"] for pct in pct_list]
        cat_cmd = " && ".join(
            f'printf "%s\\0" "{pct_id}" && cat /etc/pve/lxc/{pct_id}.conf && printf "\\0"' for pct_id in pct_ids
        )
        stdout = utils.execute_on_machine(config_ssh, cat_cmd)

        # Split by null bytes: [id, content, id, content, ...]
        parts = stdout.split("\0")
        configs = {}
        for i in range(0, len(parts) - 1, 2):
            vmid = parts[i].strip()
            content = parts[i + 1]
            if vmid:
                configs[vmid] = pct_config_parser(content)

        targets = []
        for pct in pct_list:
            pct_id = pct["VMID"]
            stdout = utils.execute_on_machine(config_ssh, f"pct config {pct_id}")
            pct_info = pct_config_parser(stdout)
            targets.append(
                target.ProxmoxCT(
                    pct_id=pct["VMID"],
                    pct_ip=IPv4Address(pct_info["ip"]),
                    pct_name=pct["Name"],
                    pct_status=pct["Status"],
                    node_name=hostname,
                    node_ip=IPv4Address(config_ssh.split("@")[1]),
                    ostype=pct_info["ostype"],
                )
            )
        return targets


_TYPE_MAP: dict[str, type[Connector]] = {
    ConnectorType.caddy.value: Caddy,
    ConnectorType.linux_machine.value: LinuxMachine,
    ConnectorType.proxmox.value: Proxmox,
}
