from abc import ABC, abstractmethod
from pathlib import Path
from string import Template
from typing import Literal
from xml.etree import ElementTree as ET

import yaml
from pydantic import BaseModel, ConfigDict

from src.classes.alert import SentAlert
from src.classes.enums import AlertConnectorType


class AlertConnector(BaseModel, ABC):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)
    name: str
    type: AlertConnectorType

    @abstractmethod
    def send(self, alert: SentAlert) -> None: ...

    def _render(self, template: str, alert: SentAlert) -> str:
        """Substitute $variable placeholders. Available: $alert_name, $pipeline_name,
        $target_id, $target_name, $signal, $url, $triggered_at."""
        return Template(template).safe_substitute(
            alert_name=alert.alert_name,
            pipeline_name=alert.pipeline_name,
            target_id=alert.target_id,
            target_name=alert.target_name or alert.target_id,
            signal=alert.signal.value,
            url=alert.url,
            triggered_at=alert.triggered_at.isoformat(),
        )

    @staticmethod
    def from_str(content: str) -> "AlertConnector":
        data = yaml.safe_load(content)
        name, conf = next(iter(data.items()))
        cls = _REGISTRY[AlertConnectorType(conf.pop("type"))]
        return cls.model_validate({"name": name, **conf})

    def to_str(self) -> str:
        data = self.model_dump(mode="json")
        name = data.pop("name")
        data = {k: v for k, v in data.items() if v is not None}
        return yaml.dump({name: data}, default_flow_style=False).strip()


class RSSConnector(AlertConnector):
    type: Literal[AlertConnectorType.rss] = AlertConnectorType.rss
    feed_path: Path
    title_template: str = "[$signal] $pipeline_name — $target_name"
    description_template: str = (
        "Alert '$alert_name' fired with signal $signal " "for $target_name on pipeline $pipeline_name.\n$url"
    )
    max_items: int = 50

    def send(self, alert: SentAlert) -> None:
        from email.utils import formatdate

        feed_path = Path(self.feed_path)
        if feed_path.exists():
            root = ET.parse(feed_path).getroot()
        else:
            feed_path.parent.mkdir(parents=True, exist_ok=True)
            root = ET.Element("rss", version="2.0")
            ch = ET.SubElement(root, "channel")
            ET.SubElement(ch, "title").text = "PipeChecker Alerts"
            ET.SubElement(ch, "link").text = alert.url
            ET.SubElement(ch, "description").text = "PipeChecker alert notifications"

        channel = root.find("channel")
        assert channel is not None
        item = ET.Element("item")
        ET.SubElement(item, "title").text = self._render(self.title_template, alert)
        ET.SubElement(item, "description").text = self._render(self.description_template, alert)
        ET.SubElement(item, "link").text = alert.url
        ET.SubElement(item, "pubDate").text = formatdate(alert.triggered_at.timestamp())
        ET.SubElement(item, "guid").text = f"{alert.pipeline_name}/{alert.target_id}/{alert.triggered_at.isoformat()}"

        # Insert newest first, then trim to max_items
        first_item_pos = sum(1 for c in channel if c.tag != "item")
        channel.insert(first_item_pos, item)
        for old in channel.findall("item")[self.max_items :]:
            channel.remove(old)

        ET.indent(root)
        ET.ElementTree(root).write(feed_path, encoding="unicode", xml_declaration=True)


class WebhookConnector(AlertConnector):
    type: Literal[AlertConnectorType.webhook] = AlertConnectorType.webhook
    url: str
    headers: dict[str, str] = {}
    # JSON body template — use $variable placeholders, literal $ as $$
    body_template: str = '{"text": "[$signal] $pipeline_name / $target_name — $url"}'

    def send(self, alert: SentAlert) -> None:
        import json

        import httpx

        body = json.loads(self._render(self.body_template, alert))
        httpx.post(self.url, json=body, headers=self.headers, timeout=10)


class DiscordConnector(WebhookConnector):
    type: Literal[AlertConnectorType.discord] = AlertConnectorType.discord
    # Discord markdown — \n must be a real newline in the rendered string
    body_template: str = '{"content": "**[$signal]** `$pipeline_name` / $target_name\\n$url"}'


_REGISTRY: dict[AlertConnectorType, type[AlertConnector]] = {
    AlertConnectorType.rss: RSSConnector,
    AlertConnectorType.webhook: WebhookConnector,
    AlertConnectorType.discord: DiscordConnector,
}
