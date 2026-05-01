from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, field_validator

from src import config
from src.classes.enums import Status


class AlertConfig(BaseModel):
    name: str
    # None means the alert applies to all pipelines
    pipeline: str | None = None
    on_signals: list[Status] = [Status.fail, Status.crashed]
    # Name of an AlertConnector to dispatch through; None = record history only
    connector: str | None = None

    @field_validator("name")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Alert name must be a non-empty string.")
        return v

    @field_validator("on_signals")
    @classmethod
    def at_least_one(cls, v: list[Status]) -> list[Status]:
        if not v:
            raise ValueError("on_signals must contain at least one status.")
        return v


@dataclass
class SentAlert:
    alert_name: str
    pipeline_name: str
    target_id: str
    target_name: str | None
    signal: Status
    url: str = field(default_factory=lambda: config.BASE_URL)
    triggered_at: datetime = field(default_factory=datetime.now)
