from abc import ABC, abstractmethod
from dataclasses import dataclass
from ipaddress import IPv4Address


@dataclass
class Target(ABC):
    @abstractmethod
    def id(self) -> str:
        pass

    @property
    @abstractmethod
    def config(self) -> dict:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass


@dataclass
class Url(Target):
    url: str

    @property
    def id(self):
        return self.url

    @property
    def config(self) -> dict:
        return {"url": self.url}

    @property
    def name(self) -> str:
        return self.url.split("://")[-1].split("/")[0]


@dataclass
class ProxmoxCT(Target):
    pct_id: int
    pct_ip: IPv4Address
    pct_name: str
    node_name: str
    node_ip: IPv4Address
    pct_status: str
    ostype: str

    @property
    def ssh_addr(self):
        # TODO maybe have a way to change the user for ssh. I don't think it can be done via `pct list`
        return f"root@{self.node_ip}"

    @property
    def id(self):
        return self.pct_id

    @property
    def config(self) -> dict:
        return {
            "pct_id": self.pct_id,
            "pct_ip": str(self.pct_ip),
            "pct_name": self.pct_name,
            "node_name": self.node_name,
            "node_ip": str(self.node_ip),
            "pct_status": self.pct_status,
            "ostype": self.ostype,
        }

    @property
    def name(self):
        return f"[{self.pct_id}] {self.pct_name}"


@dataclass
class RemoteLinuxMachine(Target):
    machine_ip: IPv4Address
    user: str
    exec_dir: str
    hostname: str

    @property
    def ssh_addr(self):
        return f"{self.user}@{self.machine_ip}"

    @property
    def id(self):
        return f"{self.ssh_addr}:{self.exec_dir}"

    @property
    def config(self) -> dict:
        return {
            "machine_ip": self.machine_ip,
            "user": self.user,
            "exec_dir": self.exec_dir,
        }

    @property
    def name(self):
        return f"{self.hostname} ({self.user})"
