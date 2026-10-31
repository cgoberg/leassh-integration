"""Constants for the Leassh integration."""

DOMAIN = "leassh"

# Config flow / entry keys (canonical naming per docs/connectors-program-plan.md §7)
CONF_SERVER_URL = "server_url"
CONF_API_TOKEN = "api_token"
CONF_NOTIFY_TARGETS = "notify_targets"

# Server wire protocol (mirrors server/src/connections + http_api.rs)
DEFAULT_PORT = 8790
PROTOCOL_VERSION = 1
WS_PATH = "/api/ws"
HEARTBEAT_INTERVAL_SECS = 30  # server pings; published in hello
STALE_AFTER_SECS = 75  # 2x heartbeat + slack -> consider connection dead
ALERT_AFTER_SECS = 300  # stale for 5 minutes -> companion-app alert

# HA event fired for every server-side leassh_event envelope. Payload
# carries `kind` plus the event data.
EVENT_LEASSH = "leassh_event"

# Leassh server pushes these event kinds (server/src/connections/events.rs)
KIND_NOTIFY = "notify"

ATTR_KIND = "kind"
ATTR_DATA = "data"

# Per-child family sensors. These are fixed identities for nodes that appear
# in the family summary; the coordinator's lifecycle pruner must keep them.
FAMILY_SENSOR_KEYS = (
    "screen_time_today",
    "screen_time_status",
    "bedtime_status",
)
