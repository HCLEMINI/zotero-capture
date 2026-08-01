"""Wire-protocol constants shared with src/browserExt/claude-bridge.js.

Both ends of the WebSocket speak the same JSON envelope:
    {v:1, id?, type, action?, data?, ts}
where type ∈ {auth, auth_ok, request, response, notification, error, heartbeat}.
"""

PROTOCOL_VERSION = 1
WS_PATH = "/bridge"


class Action:
    PING = "ping"
    GET_STATUS = "get_status"
    CAPTURE_ACTIVE_TAB = "capture_active_tab"
    CAPTURE_URL = "capture_url"
    CAPTURE_URLS = "capture_urls"


class ErrorType:
    NO_TRANSLATOR = "no_translator"
    TRANSLATOR_FAILURE = "translator_failure"
    ZOTERO_OFFLINE = "zotero_offline"
    TIMEOUT = "timeout"
    EXTENSION_DISCONNECTED = "extension_disconnected"
    PORT_IN_USE = "port_in_use"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


# Defaults mirrored in claude-bridge.js
CAPTURE_URL_DEFAULT_CONCURRENCY = 3
CAPTURE_URL_MAX_CONCURRENCY = 5
CAPTURE_URLS_HARD_MAX = 50

# --- Batch / PDF-verify defaults ---
# Problem 4 (CNKI throttling): single window ~30-40 PDFs at concurrency 4-5;
# batches of <=18 with inter-batch sleep avoid tripping the rate limit.
DEFAULT_BATCH_SIZE = 18
DEFAULT_BATCH_DELAY = 10          # seconds to sleep between batches
DEFAULT_BATCH_CONCURRENCY = 4     # per-batch translator concurrency sent to the extension
DEFAULT_DROPOFF_THRESHOLD = 0.5   # real-pdf success ratio below this == throttling cliff
DEFAULT_VERIFY_DELAY = 8          # seconds to wait for async PDF download before verifying
VERIFY_ALL_HARD_MAX = 2000        # safety cap for scan_all()
