"""IM CAM control-plane sessions and source-backed camera commands."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
import platform
import re
import time
import uuid

from .client_profile import APP_VERSION, BUNDLE_ID, OEM_VID, VID, VKEY

DEFAULT_ENDPOINT = "wss://fast.jfgou.com:443/fast"
DIRECTIONS = {"up": 1, "down": 2, "left": 4, "right": 3, "stop": 0}
_COMPLEX_PASSWORD = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[A-Za-z\d\W_]{6,64}$")


class ImCamError(Exception):
    """A service operation failed; messages exclude credentials and payloads."""


@dataclass
class _ControlLease:
    context: object
    session: object
    users: int = 0


class ControlSession:
    """One authenticated socket. Requests on it are serialized."""

    def __init__(self, ws):
        self.ws = ws
        self.session_id = ""
        self._sequence = 0
        self._lock = asyncio.Lock()

    async def send(self, command, body, callee=""):
        self._sequence += 1
        req_id = f"a_biz_{uuid.uuid4().hex[:6]}_{self._sequence}"
        async with asyncio.timeout(5):
            await self.ws.send_json({
                "headers": {"callee": callee, "caller": "", "id": command,
                            "req_id": req_id, "time": int(time.time())},
                "body": body,
            })
        return req_id

    async def request(self, command, body, callee=""):
        async with self._lock:
            async with asyncio.timeout(15):
                req_id = await self.send(command, body, callee)
                while True:
                    frame = await self.ws.receive()
                    if frame.type.name in {"ERROR", "CLOSE", "CLOSED", "CLOSING"}:
                        raise ImCamError("IM CAM control connection closed")
                    if frame.type.name != "TEXT":
                        continue
                    try:
                        message = json.loads(frame.data)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(message, dict):
                        continue
                    headers = message.get("headers")
                    if not isinstance(headers, dict) or headers.get("req_id") != req_id:
                        continue
                    if message.get("ret") != 0:
                        raise ImCamError(f"IM CAM rejected {command} (ret={message.get('ret')})")
                    return message

    async def read_privacy(self, serial):
        reply = await self.request("pub_dp_get", {"oem_vid": OEM_VID, "sn": serial, "dpids": [532]}, serial)
        rows = reply.get("body")
        if not isinstance(rows, list):
            raise ImCamError("IM CAM privacy response has no DP list")
        for row in rows:
            if not isinstance(row, dict) or row.get("id") != 532:
                continue
            try:
                value = json.loads(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
            enabled = value.get("enable") if isinstance(value, dict) else None
            if isinstance(enabled, bool):
                return enabled
        return None

    async def set_privacy(self, serial, enabled):
        await self.request("pub_dp_set", [{"id": 532, "time": int(time.time() * 1000),
                           "value": json.dumps({"enable": enabled})}], serial)
        return await self.read_privacy(serial)

    async def ptz(self, serial, direction):
        if direction not in DIRECTIONS:
            raise ValueError("Unknown PTZ direction")
        await self.send("pub_dp_act", {"act": 1, "dp_list": [{
            "id": 537, "time": int(time.time() * 1000),
            "value": json.dumps({"direction": DIRECTIONS[direction]}),
        }]}, serial)


class ImCamApi:
    def __init__(self, http_session, account, password):
        self.http = http_session
        self.account = account
        self.password = password
        self.endpoint = DEFAULT_ENDPOINT
        self._session_lock = asyncio.Lock()
        self._lease = None

    @asynccontextmanager
    async def control_session(self):
        # A live view holds a lease. Polling and controls reuse its authenticated
        # socket rather than signing in again and risking invalidating its session.
        async with self._session_lock:
            lease = self._lease
            if lease is None or lease.session.ws.closed:
                context = self._new_control_session()
                session = await context.__aenter__()
                lease = self._lease = _ControlLease(context, session)
            lease.users += 1
        try:
            yield lease.session
        finally:
            async with self._session_lock:
                lease.users -= 1
                if lease.users == 0:
                    if self._lease is lease:
                        self._lease = None
                    await lease.context.__aexit__(None, None, None)

    @asynccontextmanager
    async def _new_control_session(self):
        endpoint = self.endpoint
        for attempt in range(2):
            async with asyncio.timeout(30):
                ws = await self.http.ws_connect(endpoint, headers={"Origin": "https://fast.jfgou.com"}, heartbeat=30)
            session = ControlSession(ws)
            try:
                await ws.send_json({"headers": {"id": "pub_heartbeat", "req_id": "1", "time": 1}})
                reply = await session.request("cli_login", {
                    "account": self.account, "passwd": hashlib.md5(self.password.encode()).hexdigest(),
                    "sessid": "", "simple_passwd": not bool(_COMPLEX_PASSWORD.fullmatch(self.password)),
                    "language_type": 1, "service_type": 0, "device_token": "", "sys_version": platform.release(),
                    "bundle_id": BUNDLE_ID, "model": "Home Assistant", "net": 1, "os": 2,
                    "version": APP_VERSION, "vid": VID, "vkey": VKEY, "oem_vid": OEM_VID, "udid": "",
                })
                body = reply.get("body")
                if not isinstance(body, dict):
                    raise ImCamError("IM CAM login response has no body")
                if body.get("switch_region"):
                    urls = body.get("url", [])
                    regional = next((url for url in urls if isinstance(url, str) and url.startswith("wss://")), None)
                    if attempt or not regional or regional == endpoint:
                        raise ImCamError("IM CAM requested an invalid region change")
                    endpoint = regional
                    continue
                session.session_id = body.get("sessid", "")
                if not session.session_id:
                    raise ImCamError("IM CAM login response has no session")
                self.endpoint = endpoint
                yield session
                return
            finally:
                await ws.close()
        raise ImCamError("IM CAM requested multiple region changes")
