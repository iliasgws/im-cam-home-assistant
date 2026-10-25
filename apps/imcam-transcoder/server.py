"""Private HLS service for Home Assistant's IM CAM integration."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import signal
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from aiohttp import web

PORT = 8787
IDLE_TIMEOUT = 75
START_TIMEOUT = 55
MAX_STREAMS = 2
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,}$")
SEGMENT_RE = re.compile(r"^segment\d{5}\.ts$")
ROOT = Path(tempfile.gettempdir()) / "imcam-hls"
_LOGGER = logging.getLogger("imcam_transcoder")


class TranscoderError(Exception):
    """An expected, safe-to-return error."""


@dataclass
class Stream:
    token: str
    serial: str
    directory: Path
    process: asyncio.subprocess.Process
    last_access: float
    log_task: asyncio.Task | None = None


class Transcoder:
    def __init__(self, api_key: str):
        self.api_key = api_key.encode()
        self.streams: dict[str, Stream] = {}
        self.serials: dict[str, str] = {}
        self.lock = asyncio.Lock()
        self.cleanup_task: asyncio.Task | None = None

    async def start(self):
        self.cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def close(self):
        if self.cleanup_task:
            self.cleanup_task.cancel()
            await asyncio.gather(self.cleanup_task, return_exceptions=True)
        async with self.lock:
            streams = tuple(self.streams.values())
            self.streams.clear()
            self.serials.clear()
        await asyncio.gather(*(self._stop(stream) for stream in streams), return_exceptions=True)

    def authorized(self, request: web.Request) -> bool:
        value = request.headers.get("Authorization", "")
        scheme, _, token = value.partition(" ")
        return (
            len(self.api_key) >= 32
            and scheme.lower() == "bearer"
            and hmac.compare_digest(token.encode(), self.api_key)
        )

    async def create_stream(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            raise web.HTTPUnauthorized()
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError) as err:
            raise web.HTTPBadRequest(text="Invalid stream request") from err
        required = ("wss", "accessId", "secret", "sessionId", "serial", "permitCode", "quality")
        if not isinstance(body, dict) or any(not isinstance(body.get(key), str) for key in required):
            raise web.HTTPBadRequest(text="Invalid stream request")
        if not body["wss"].startswith("wss://") or body["quality"] not in {"SD", "HD"}:
            raise web.HTTPBadRequest(text="Invalid stream request")

        async with self.lock:
            existing_token = self.serials.get(body["serial"])
            existing = self.streams.get(existing_token or "")
            if existing and existing.process.returncode is None:
                existing.last_access = time.monotonic()
                existing.process.stdin.write(
                    (json.dumps({"type": "quality", "quality": body["quality"]}) + "\n").encode()
                )
                await existing.process.stdin.drain()
                stream = existing
            else:
                if existing_token:
                    stale = self.streams.pop(existing_token, None)
                    self.serials.pop(body["serial"], None)
                    if stale:
                        shutil.rmtree(stale.directory, ignore_errors=True)
                if len(self.streams) >= MAX_STREAMS:
                    raise web.HTTPServiceUnavailable(text="The transcoder is already at its stream limit.")
                token = secrets.token_urlsafe(32)
                directory = ROOT / token
                directory.mkdir(parents=True, mode=0o700)
                body["hls_dir"] = str(directory)
                body["quality_code"] = 2 if body["quality"] == "HD" else 1
                process = await asyncio.create_subprocess_exec(
                    "python3", "/opt/imcam/imcam_live_gst.py",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                log_task = asyncio.create_task(self._read_helper_logs(process.stderr))
                process.stdin.write((json.dumps(body, separators=(",", ":")) + "\n").encode())
                await process.stdin.drain()
                stream = Stream(token, body["serial"], directory, process, time.monotonic(), log_task)
                self.streams[token] = stream
                self.serials[stream.serial] = token

        return web.json_response({"id": stream.token})

    async def set_quality(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            raise web.HTTPUnauthorized()
        token = request.match_info["token"]
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError) as err:
            raise web.HTTPBadRequest(text="Invalid quality request") from err
        quality = body.get("quality") if isinstance(body, dict) else None
        if quality not in {"SD", "HD"}:
            raise web.HTTPBadRequest(text="Invalid quality request")
        async with self.lock:
            stream = self.streams.get(token)
            if stream is None or stream.process.returncode is not None:
                raise web.HTTPNotFound()
            stream.last_access = time.monotonic()
            process = stream.process
            process.stdin.write((json.dumps({"type": "quality", "quality": quality}) + "\n").encode())
            await process.stdin.drain()
        return web.json_response({"ok": True})

    async def close_stream(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            raise web.HTTPUnauthorized()
        await self._discard(request.match_info["token"])
        return web.json_response({"ok": True})

    async def serve_hls(self, request: web.Request) -> web.StreamResponse:
        token, name = request.match_info["token"], request.match_info["name"]
        if not TOKEN_RE.fullmatch(token) or not (name == "index.m3u8" or SEGMENT_RE.fullmatch(name)):
            raise web.HTTPNotFound()
        deadline = time.monotonic() + START_TIMEOUT
        while True:
            async with self.lock:
                stream = self.streams.get(token)
                if stream is None:
                    raise web.HTTPNotFound()
                stream.last_access = time.monotonic()
                path = stream.directory / name
                process = stream.process
            if path.is_file() and path.stat().st_size > 0:
                break
            if name != "index.m3u8" or process.returncode is not None:
                raise web.HTTPBadGateway(text="The transcoded camera stream is not ready.")
            if time.monotonic() >= deadline:
                raise web.HTTPGatewayTimeout(text="The camera did not produce a transcoded stream in time.")
            await asyncio.sleep(0.25)
        content_type = "application/vnd.apple.mpegurl" if name.endswith("m3u8") else "video/mp2t"
        return web.FileResponse(path, headers={"Content-Type": content_type, "Cache-Control": "no-store"})

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(15)
            cutoff = time.monotonic() - IDLE_TIMEOUT
            async with self.lock:
                expired = [token for token, stream in self.streams.items()
                           if stream.last_access < cutoff or stream.process.returncode is not None]
            for token in expired:
                await self._discard_if_idle(token, cutoff)

    async def _discard(self, token: str):
        async with self.lock:
            stream = self.streams.pop(token, None)
            if stream and self.serials.get(stream.serial) == token:
                self.serials.pop(stream.serial, None)
        if stream:
            await self._stop(stream)

    async def _discard_if_idle(self, token: str, cutoff: float):
        async with self.lock:
            stream = self.streams.get(token)
            if stream is None or (
                stream.last_access >= cutoff and stream.process.returncode is None
            ):
                return
            self.streams.pop(token, None)
            if self.serials.get(stream.serial) == token:
                self.serials.pop(stream.serial, None)
        await self._stop(stream)

    @staticmethod
    async def _read_helper_logs(reader):
        if reader is None:
            return
        while line := await reader.readline():
            message = line.decode("utf-8", "replace").strip()
            # The helper marks its sanitized status/error messages. Leave raw
            # GStreamer diagnostics out of HA logs because they can include SDP.
            if message.startswith("IMCAM: "):
                _LOGGER.info("%s", message[7:500])

    @staticmethod
    async def _stop(stream: Stream):
        process = stream.process
        if process.stdin and not process.stdin.is_closing():
            process.stdin.close()
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        if stream.log_task:
            await asyncio.gather(stream.log_task, return_exceptions=True)
        shutil.rmtree(stream.directory, ignore_errors=True)


async def async_main():
    try:
        with Path("/data/options.json").open(encoding="utf-8") as options_file:
            options = json.load(options_file)
    except (OSError, json.JSONDecodeError):
        options = {}
    api_key = os.environ.get("IMCAM_TRANSCODER_API_KEY") or options.get("api_key", "")
    if not isinstance(api_key, str):
        api_key = ""
    transcoder = Transcoder(api_key)
    app = web.Application(client_max_size=128 * 1024)
    app["transcoder"] = transcoder
    app.router.add_get("/health", lambda _request: web.json_response({"ok": True}))
    app.router.add_post("/v1/streams", transcoder.create_stream)
    app.router.add_post("/v1/streams/{token}/quality", transcoder.set_quality)
    app.router.add_delete("/v1/streams/{token}", transcoder.close_stream)
    app.router.add_get("/hls/{token}/{name}", transcoder.serve_hls)
    app.on_startup.append(lambda _app: transcoder.start())
    app.on_cleanup.append(lambda _app: transcoder.close())
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass
