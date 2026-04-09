from abc import abstractmethod
from ipaddress import IPv4Address
from dataclasses import dataclass
from abc import ABC

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
    pct_status: str # Literal["running", ]
    ostype: str

    @property
    def ssh_addr(self):
        return f"root@{self.node_ip}" # TODO maybe have a way to change the user for ssh. I don't think it can be done
                                     # via `pct list`

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