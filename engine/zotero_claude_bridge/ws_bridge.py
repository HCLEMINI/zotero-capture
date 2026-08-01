"""WebSocket server that the patched Zotero Connector connects to.

Single-client (one extension), loopback-only, request/response with id matching.
Auth is loopback-trust (MVP): the extension sends an `auth` frame on open and the
server replies `auth_ok`. The server binds 127.0.0.1 so only local processes can
reach it.
"""
import asyncio
import json
import time
from typing import Any, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from .schemas import WS_PATH, ErrorType


class BridgeError(Exception):
    def __init__(self, data: Any):
        self.data = data if isinstance(data, dict) else {"error": str(data)}
        super().__init__(str(self.data))


def _now_ms() -> int:
    return int(time.time() * 1000)


class WSBridge:
    def __init__(self, host: str, port: int, logger):
        self.host = host
        self.port = port
        self.log = logger
        self._server = None
        self._ext_ws = None  # the single connected extension
        self._pending: dict[str, asyncio.Future] = {}
        self._counter = 0
        self._lock = asyncio.Lock()
        # bind_state: "starting" | "owned" | "port_in_use" | "bind_failed"
        # Problem 1: when another Claude session owns the single fixed port, this
        # server cannot bind and will never see the extension. Track that explicitly
        # so tools report port_in_use instead of silently saying extension_disconnected.
        self.bind_state = "starting"
        self.bind_error: Optional[str] = None

    def is_connected(self) -> bool:
        return self._ext_ws is not None

    def _port_is_in_use(self) -> bool:
        """Probe whether the port is currently occupied (e.g. by another bridge)."""
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            return s.connect_ex((self.host, self.port)) == 0
        finally:
            s.close()

    async def start(self) -> None:
        try:
            self._server = await websockets.serve(
                self._handler,
                self.host,
                self.port,
                ping_interval=20,
                ping_timeout=20,
                max_size=8 * 1024 * 1024,
            )
        except OSError as e:
            # Could not bind. Most likely another zcb session owns the port — only
            # one bridge can hold the Edge connection, so this server is useless.
            # Do NOT crash: the MCP stdio server stays up and every tool call will
            # report port_in_use with a fix hint (Problem 1/2).
            self.bind_state = "port_in_use" if self._port_is_in_use() else "bind_failed"
            self.bind_error = str(e)
            self.log.error(
                "WS bind failed on %s:%d (%s) — bind_state=%s",
                self.host, self.port, e, self.bind_state,
            )
            return
        self.bind_state = "owned"
        self.log.info("WS listening on ws://%s:%d%s", self.host, self.port, WS_PATH)
        await asyncio.Future()  # run forever (only reached when owned)

    async def _handler(self, ws) -> None:
        # single-client: reject extra connections
        async with self._lock:
            if self._ext_ws is not None:
                self.log.warning("rejecting extra extension connection")
                try:
                    await ws.close(code=4003, reason="another extension already connected")
                except Exception:
                    pass
                return
            self._ext_ws = ws
        peer = getattr(ws, "remote_address", "?")
        self.log.info("extension connected from %s", peer)

        try:
            # require an auth frame within 5s
            try:
                first_raw = await asyncio.wait_for(ws.recv(), 5)
                first = json.loads(first_raw)
                if first.get("type") != "auth":
                    raise ValueError("first frame must be 'auth'")
            except Exception as e:
                self.log.warning("auth failed: %s", e)
                try:
                    await ws.close(code=4001, reason="auth required")
                except Exception:
                    pass
                return
            await ws.send(json.dumps({"v": 1, "type": "auth_ok", "ts": _now_ms()}))
            self.log.info("extension authenticated")

            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                mtype = msg.get("type")
                if mtype == "heartbeat":
                    await ws.send(json.dumps({"v": 1, "type": "heartbeat", "ts": _now_ms()}))
                elif mtype == "response":
                    fut = self._pending.pop(msg.get("id"), None)
                    if fut and not fut.done():
                        fut.set_result(msg.get("data") or {})
                elif mtype == "error":
                    fut = self._pending.pop(msg.get("id"), None)
                    if fut and not fut.done():
                        fut.set_exception(BridgeError(msg.get("data") or {}))
                elif mtype == "notification":
                    self.log.info("notification: %s",
                                  json.dumps(msg.get("data"), ensure_ascii=False)[:200])
                # request/auth from extension not expected
        except ConnectionClosed:
            pass
        except Exception as e:
            self.log.exception("handler error: %s", e)
        finally:
            async with self._lock:
                if self._ext_ws is ws:
                    self._ext_ws = None
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(BridgeError({
                        "success": False,
                        "errorType": "extension_disconnected",
                        "error": "extension disconnected",
                        "items": [],
                    }))
            self._pending.clear()
            self.log.info("extension disconnected")

    def _port_in_use_result(self, action: str = "") -> dict:
        """Result returned when this server does not own the WS port (Problem 1/2)."""
        return {
            "success": False,
            "errorType": ErrorType.PORT_IN_USE,
            "bind_state": self.bind_state,
            "error": (
                f"WS bridge could not bind {self.host}:{self.port} "
                f"(bind_state={self.bind_state}"
                + (f", {self.bind_error}" if self.bind_error else "")
                + "). Another Claude session's bridge most likely owns the port, "
                "so this server will never receive the extension connection."
            ),
            "hint": (
                f"Fix: (1) taskkill the python.exe owning port {self.port} "
                "(netstat -ano | findstr :%d); (2) /mcp reconnect zotero-claude-bridge; "
                "(3) refresh any tab in Edge to wake the MV3 service worker." % self.port
            ),
            "items": [],
        }

    async def request(self, action: str, data: dict, timeout: float = 60) -> dict:
        """Send a request to the extension and await its response dict."""
        if self.bind_state != "owned":
            return self._port_in_use_result(action)
        if not self.is_connected():
            return {
                "success": False,
                "errorType": "extension_disconnected",
                "error": ("Extension not connected. Load the patched Zotero Connector "
                          "in Chrome (build/manifestv3/) and open any web page so the "
                          "service worker activates."),
                "items": [],
            }
        self._counter += 1
        rid = f"req_{self._counter}"
        fut: asyncio.Future = asyncio.Future()
        self._pending[rid] = fut
        payload = {
            "v": 1, "id": rid, "type": "request",
            "action": action, "data": data or {}, "ts": _now_ms(),
        }
        try:
            await self._ext_ws.send(json.dumps(payload))
        except Exception as e:
            self._pending.pop(rid, None)
            return {"success": False, "errorType": "extension_disconnected",
                    "error": f"send failed: {e}", "items": []}
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            return {"success": False, "errorType": "timeout",
                    "error": f"{action} timed out after {timeout}s", "items": []}
        except BridgeError as e:
            return e.data
