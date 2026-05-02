from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest
import yaml

import src.config as cfg
from src.classes.alert import AlertConfig, SentAlert
from src.classes.alert_connector import (
    AlertConnector,
    DiscordConnector,
    RSSConnector,
    WebhookConnector,
)
from src.classes.enums import AlertConnectorType, Status
from src.core import storage

# ── helpers ───────────────────────────────────────────────────────────────────


def _alert(name="test-alert", pipeline=None, on_signals=None) -> AlertConfig:
    kwargs: dict = {"name": name}
    if pipeline is not None:
        kwargs["pipeline"] = pipeline
    if on_signals is not None:
        kwargs["on_signals"] = on_signals
    return AlertConfig(**kwargs)


def _sent(
    alert_name="test-alert",
    pipeline_name="my-pipeline",
    target_id="100",
    target_name="ct-100",
    signal=Status.fail,
    url=None,
) -> SentAlert:
    kwargs: dict = dict(
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        target_id=target_id,
        target_name=target_name,
        signal=signal,
    )
    if url is not None:
        kwargs["url"] = url
    return SentAlert(**kwargs)


def _webhook(
    name="my-hook", url="https://example.com/hook", **kwargs
) -> WebhookConnector:
    return WebhookConnector(name=name, url=url, **kwargs)


def _rss(name="my-rss", feed_path="/tmp/test-feed.xml", **kwargs) -> RSSConnector:
    return RSSConnector(name=name, feed_path=feed_path, **kwargs)


# ── AlertConfig ───────────────────────────────────────────────────────────────


class TestAlertConfig:
    def test_defaults(self):
        a = AlertConfig(name="my-alert")
        assert a.pipeline is None
        assert a.on_signals == [Status.fail, Status.crashed]

    def test_explicit_fields(self):
        a = AlertConfig(name="my-alert", pipeline="prod", on_signals=[Status.warning])
        assert a.pipeline == "prod"
        assert a.on_signals == [Status.warning]

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError):
            AlertConfig(name="   ")

    def test_empty_on_signals_rejected(self):
        with pytest.raises(ValueError):
            AlertConfig(name="x", on_signals=[])

    def test_multiple_signals(self):
        a = AlertConfig(
            name="x", on_signals=[Status.warning, Status.fail, Status.crashed]
        )
        assert len(a.on_signals) == 3


# ── SentAlert ─────────────────────────────────────────────────────────────────


class TestSentAlert:
    def test_url_defaults_to_base_url(self):
        s = _sent()
        assert s.url == cfg.BASE_URL

    def test_url_can_be_overridden(self):
        s = _sent(url="http://prod.example.com:9000/job/abc")
        assert s.url == "http://prod.example.com:9000/job/abc"

    def test_triggered_at_defaults_to_now(self):
        before = datetime.now()
        s = _sent()
        after = datetime.now()
        assert before <= s.triggered_at <= after

    def test_explicit_triggered_at(self):
        ts = datetime(2024, 6, 1, 12, 0, 0)
        s = SentAlert(
            alert_name="a",
            pipeline_name="p",
            target_id="1",
            target_name="ct-1",
            signal=Status.warning,
            triggered_at=ts,
        )
        assert s.triggered_at == ts

    def test_none_target_name_allowed(self):
        s = _sent(target_name=None)
        assert s.target_name is None


# ── Alert storage ─────────────────────────────────────────────────────────────


class TestLoadAlerts:
    def test_empty_when_file_absent(self):
        assert storage.load_alerts() == []

    def test_returns_saved_alerts(self):
        storage.save_alert(_alert("a1"))
        storage.save_alert(_alert("a2", pipeline="prod"))
        names = {a.name for a in storage.load_alerts()}
        assert names == {"a1", "a2"}

    def test_full_roundtrip(self):
        storage.save_alert(_alert("a", pipeline="x", on_signals=[Status.warning]))
        [a] = storage.load_alerts()
        assert a.pipeline == "x"
        assert a.on_signals == [Status.warning]

    def test_none_pipeline_roundtrip(self):
        storage.save_alert(_alert("a", pipeline=None))
        [a] = storage.load_alerts()
        assert a.pipeline is None


class TestSaveAlert:
    def test_persists_to_yaml(self):
        storage.save_alert(_alert("my-alert"))
        raw = yaml.safe_load(cfg.ALERT_FILE.read_text())
        assert any(a["name"] == "my-alert" for a in raw)

    def test_duplicate_name_rejected(self):
        storage.save_alert(_alert("dup"))
        with pytest.raises(ValueError, match="already exists"):
            storage.save_alert(_alert("dup"))

    def test_multiple_alerts_accumulate(self):
        for i in range(3):
            storage.save_alert(_alert(f"a{i}"))
        assert len(storage.load_alerts()) == 3


class TestUpdateAlert:
    def test_updates_existing(self):
        storage.save_alert(_alert("a", pipeline="old"))
        storage.update_alert(_alert("a", pipeline="new"))
        [a] = storage.load_alerts()
        assert a.pipeline == "new"

    def test_unknown_name_raises(self):
        with pytest.raises(KeyError):
            storage.update_alert(_alert("ghost"))

    def test_only_matching_entry_changed(self):
        storage.save_alert(_alert("a", pipeline="pa"))
        storage.save_alert(_alert("b", pipeline="pb"))
        storage.update_alert(_alert("a", pipeline="pa-updated"))
        alerts = {a.name: a for a in storage.load_alerts()}
        assert alerts["a"].pipeline == "pa-updated"
        assert alerts["b"].pipeline == "pb"


class TestDeleteAlert:
    def test_removes_alert(self):
        storage.save_alert(_alert("to-del"))
        storage.delete_alert("to-del")
        assert storage.load_alerts() == []

    def test_unknown_name_raises(self):
        with pytest.raises(KeyError):
            storage.delete_alert("ghost")

    def test_only_target_removed(self):
        storage.save_alert(_alert("keep"))
        storage.save_alert(_alert("drop"))
        storage.delete_alert("drop")
        [a] = storage.load_alerts()
        assert a.name == "keep"


# ── Alert connector storage ───────────────────────────────────────────────────


class TestLoadAlertConnectors:
    def test_empty_when_file_absent(self):
        assert storage.load_alert_connectors() == []

    def test_returns_saved_connectors(self):
        storage.save_alert_connector(_webhook("h1"))
        storage.save_alert_connector(_webhook("h2", url="https://other.com"))
        assert len(storage.load_alert_connectors()) == 2

    def test_type_preserved(self):
        storage.save_alert_connector(
            DiscordConnector(name="dc", url="https://discord.com/x")
        )
        [c] = storage.load_alert_connectors()
        assert isinstance(c, DiscordConnector)


class TestSaveAlertConnector:
    def test_persists_to_yaml(self):
        storage.save_alert_connector(_webhook("my-hook"))
        raw = yaml.safe_load(cfg.ALERT_CONNECTOR_FILE.read_text())
        assert "my-hook" in raw

    def test_duplicate_name_rejected(self):
        storage.save_alert_connector(_webhook("dup"))
        with pytest.raises(ValueError, match="already exists"):
            storage.save_alert_connector(_webhook("dup"))


class TestUpdateAlertConnector:
    def test_updates_existing(self):
        storage.save_alert_connector(_webhook("h", url="https://old.com"))
        storage.update_alert_connector(_webhook("h", url="https://new.com"))
        [c] = storage.load_alert_connectors()
        assert c.url == "https://new.com"

    def test_unknown_name_raises(self):
        with pytest.raises(KeyError):
            storage.update_alert_connector(_webhook("ghost"))

    def test_only_matching_entry_changed(self):
        storage.save_alert_connector(_webhook("a", url="https://a.com"))
        storage.save_alert_connector(_webhook("b", url="https://b.com"))
        storage.update_alert_connector(_webhook("a", url="https://a-new.com"))
        connectors = {c.name: c for c in storage.load_alert_connectors()}
        assert connectors["a"].url == "https://a-new.com"
        assert connectors["b"].url == "https://b.com"


class TestDeleteAlertConnector:
    def test_removes_connector(self):
        storage.save_alert_connector(_webhook("to-del"))
        storage.delete_alert_connector("to-del")
        assert storage.load_alert_connectors() == []

    def test_unknown_name_raises(self):
        with pytest.raises(KeyError):
            storage.delete_alert_connector("ghost")

    def test_only_target_removed(self):
        storage.save_alert_connector(_webhook("keep"))
        storage.save_alert_connector(_webhook("drop"))
        storage.delete_alert_connector("drop")
        [c] = storage.load_alert_connectors()
        assert c.name == "keep"


# ── _render ───────────────────────────────────────────────────────────────────


class TestRender:
    def test_all_variables_substituted(self):
        c = _webhook()
        s = _sent(
            alert_name="al",
            pipeline_name="pp",
            target_id="42",
            target_name="ct-42",
            signal=Status.fail,
            url="http://x/job/1",
        )
        result = c._render(
            "$alert_name $pipeline_name $target_id $target_name $signal $url $triggered_at",
            s,
        )
        for expected in ("al", "pp", "42", "ct-42", "fail", "http://x/job/1"):
            assert expected in result

    def test_none_target_name_falls_back_to_id(self):
        c = _webhook()
        s = _sent(target_id="99", target_name=None)
        assert c._render("$target_name", s) == "99"

    def test_unknown_placeholder_left_intact(self):
        c = _webhook()
        assert "$unknown" in c._render("$unknown", _sent())

    def test_signal_is_value_not_enum(self):
        c = _webhook()
        result = c._render("$signal", _sent(signal=Status.warning))
        assert result == "warning"


# ── RSSConnector ──────────────────────────────────────────────────────────────


class TestRSSConnector:
    def test_creates_feed_file(self, tmp_path):
        path = tmp_path / "feed.xml"
        RSSConnector(name="r", feed_path=path).send(_sent())
        assert path.exists()

    def test_feed_is_valid_rss(self, tmp_path):
        path = tmp_path / "feed.xml"
        RSSConnector(name="r", feed_path=path).send(_sent())
        root = ET.parse(path).getroot()
        assert root.tag == "rss"
        assert root.find("channel") is not None
        assert root.find("channel/item") is not None

    def test_item_title_uses_template(self, tmp_path):
        path = tmp_path / "feed.xml"
        RSSConnector(name="r", feed_path=path, title_template="TITLE-$signal").send(
            _sent(signal=Status.fail)
        )
        assert ET.parse(path).getroot().find("channel/item/title").text == "TITLE-fail"

    def test_item_link_is_alert_url(self, tmp_path):
        path = tmp_path / "feed.xml"
        RSSConnector(name="r", feed_path=path).send(_sent(url="http://host/job/123"))
        assert (
            ET.parse(path).getroot().find("channel/item/link").text
            == "http://host/job/123"
        )

    def test_second_send_prepends_newest(self, tmp_path):
        path = tmp_path / "feed.xml"
        c = RSSConnector(name="r", feed_path=path, title_template="$signal")
        c.send(_sent(signal=Status.warning))
        c.send(_sent(signal=Status.fail))
        items = ET.parse(path).getroot().findall("channel/item")
        assert items[0].find("title").text == "fail"
        assert items[1].find("title").text == "warning"

    def test_max_items_enforced(self, tmp_path):
        path = tmp_path / "feed.xml"
        c = RSSConnector(name="r", feed_path=path, max_items=2)
        for sig in (Status.ok, Status.warning, Status.fail):
            c.send(_sent(signal=sig))
        assert len(ET.parse(path).getroot().findall("channel/item")) == 2

    def test_max_items_keeps_newest(self, tmp_path):
        path = tmp_path / "feed.xml"
        c = RSSConnector(
            name="r", feed_path=path, max_items=2, title_template="$signal"
        )
        for sig in (Status.ok, Status.warning, Status.fail):
            c.send(_sent(signal=sig))
        titles = [
            i.find("title").text
            for i in ET.parse(path).getroot().findall("channel/item")
        ]
        assert "ok" not in titles

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "feed.xml"
        RSSConnector(name="r", feed_path=path).send(_sent())
        assert path.exists()


# ── WebhookConnector ──────────────────────────────────────────────────────────


class TestWebhookConnector:
    def test_posts_to_url(self):
        c = _webhook(url="https://hooks.example.com/test")
        with patch("httpx.post") as mock_post:
            c.send(_sent())
        assert mock_post.call_args[0][0] == "https://hooks.example.com/test"

    def test_body_is_rendered_json(self):
        c = WebhookConnector(
            name="h",
            url="https://x.com",
            body_template='{"msg": "$signal on $pipeline_name"}',
        )
        with patch("httpx.post") as mock_post:
            c.send(_sent(signal=Status.fail, pipeline_name="my-pipe"))
        assert mock_post.call_args[1]["json"] == {"msg": "fail on my-pipe"}

    def test_headers_forwarded(self):
        c = WebhookConnector(
            name="h",
            url="https://x.com",
            headers={"Authorization": "Bearer token123"},
        )
        with patch("httpx.post") as mock_post:
            c.send(_sent())
        assert mock_post.call_args[1]["headers"]["Authorization"] == "Bearer token123"

    def test_timeout_is_set(self):
        with patch("httpx.post") as mock_post:
            _webhook().send(_sent())
        assert mock_post.call_args[1]["timeout"] == 10


# ── DiscordConnector ──────────────────────────────────────────────────────────


class TestDiscordConnector:
    def test_different_default_template_than_webhook(self):
        dc = DiscordConnector(name="x", url="https://discord.com/x")
        wh = WebhookConnector(name="x", url="https://x.com")
        assert dc.body_template != wh.body_template

    def test_send_posts_to_discord_url(self):
        c = DiscordConnector(name="dc", url="https://discord.com/api/webhooks/1/abc")
        with patch("httpx.post") as mock_post:
            c.send(_sent())
        assert "discord.com" in mock_post.call_args[0][0]

    def test_body_contains_signal(self):
        c = DiscordConnector(name="dc", url="https://discord.com/api/webhooks/1/abc")
        with patch("httpx.post") as mock_post:
            c.send(_sent(signal=Status.crashed))
        assert "crashed" in str(mock_post.call_args[1]["json"])

    def test_inherits_webhook_headers(self):
        c = DiscordConnector(
            name="dc",
            url="https://discord.com/x",
            headers={"X-Custom": "val"},
        )
        with patch("httpx.post") as mock_post:
            c.send(_sent())
        assert mock_post.call_args[1]["headers"]["X-Custom"] == "val"


# ── Serialization roundtrip ───────────────────────────────────────────────────


class TestAlertConnectorSerialization:
    def test_webhook_roundtrip(self):
        c = WebhookConnector(
            name="my-hook",
            url="https://example.com",
            headers={"X-Token": "abc"},
            body_template='{"text": "$signal"}',
        )
        c2 = AlertConnector.from_str(c.to_str())
        assert isinstance(c2, WebhookConnector)
        assert c2.url == c.url
        assert c2.headers == c.headers
        assert c2.body_template == c.body_template

    def test_discord_roundtrip(self):
        c = DiscordConnector(name="dc", url="https://discord.com/x")
        c2 = AlertConnector.from_str(c.to_str())
        assert isinstance(c2, DiscordConnector)
        assert c2.url == c.url
        assert c2.body_template == c.body_template

    def test_rss_roundtrip(self, tmp_path):
        path = tmp_path / "feed.xml"
        c = RSSConnector(name="rss", feed_path=path, max_items=10)
        c2 = AlertConnector.from_str(c.to_str())
        assert isinstance(c2, RSSConnector)
        assert Path(c2.feed_path) == Path(c.feed_path)
        assert c2.max_items == c.max_items

    def test_from_str_dispatches_to_correct_class(self):
        for connector, expected_cls in [
            (WebhookConnector(name="x", url="https://x.com"), WebhookConnector),
            (DiscordConnector(name="x", url="https://x.com"), DiscordConnector),
        ]:
            assert type(AlertConnector.from_str(connector.to_str())) is expected_cls

    def test_type_field_present_in_yaml(self):
        c = _webhook()
        raw = yaml.safe_load(c.to_str())
        assert raw[c.name]["type"] == AlertConnectorType.webhook.value
