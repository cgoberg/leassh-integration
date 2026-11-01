"""notify.leassh delivery paths: targets, urgency, ack URL and setup wiring."""

from __future__ import annotations

from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.leassh.const import CONF_NOTIFY_TARGETS, DOMAIN
from custom_components.leassh.notify import LeasshNotifyEntity, async_setup_entry


def _entry(options: dict | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={},
        options=options or {},
        entry_id="entry1",
    )


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(base_url="http://127.0.0.1:8790"))


async def test_delivers_to_configured_targets_with_critical_sound(hass) -> None:
    calls = []

    async def capture(call):
        calls.append(call.data)

    hass.services.async_register("notify", "mobile_app_phone", capture)
    entry = _entry({CONF_NOTIFY_TARGETS: ["mobile_app_phone"]})
    entity = LeasshNotifyEntity(hass, entry, _runtime())
    await entity.async_deliver(
        "Alert", "body", {"level": "urgent", "ack_url": "https://example/ack"}
    )
    assert calls[0]["title"] == "Alert"
    assert calls[0]["data"]["push"]["sound"]["critical"] == 1
    assert calls[0]["data"]["url"] == "https://example/ack"

    await entity.async_send_message("hello", "Title")
    assert calls[1]["title"] == "Title"
    assert calls[1]["message"] == "hello"


async def test_defaults_to_all_mobile_app_targets(hass) -> None:
    calls = []

    async def capture(call):
        calls.append(call.data)

    hass.services.async_register("notify", "mobile_app_phone", capture)
    hass.services.async_register("notify", "mobile_app_tablet", capture)
    hass.services.async_register("notify", "email", capture)
    entity = LeasshNotifyEntity(hass, _entry(), _runtime())
    await entity.async_deliver("Info", "body", {})
    assert len(calls) == 2
    assert all("critical" not in call["data"]["push"].get("sound", {}) for call in calls)


async def test_no_targets_is_a_warning_not_a_crash(hass, caplog) -> None:
    entity = LeasshNotifyEntity(hass, _entry(), _runtime())
    await entity.async_deliver("Info", "body", {})
    assert "no companion targets" in caplog.text


async def test_setup_entry_wires_both_delivery_paths(hass) -> None:
    entry = _entry()
    runtime = _runtime()
    runtime.send_notification = None
    entry.runtime_data = runtime
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(hass, entry, add_entities)
    assert len(added) == 1
    assert runtime.send_notification is not None
    assert hass.data[DOMAIN]["outage_notifier"] is not None
