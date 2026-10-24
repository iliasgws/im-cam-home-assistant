"""Browser-to-camera WebRTC signaling bridge for IM CAM cameras."""

from __future__ import annotations

import json
import hashlib
import base64
import asyncio
import uuid
import time
import re
from collections.abc import Mapping
from contextlib import AsyncExitStack, suppress
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from webrtc_models import RTCIceCandidateInit, RTCConfiguration, RTCIceServer

from homeassistant.components.camera import (
    WebRTCAnswer,
    WebRTCCandidate,
    WebRTCClientConfiguration,
    WebRTCError,
)
from homeassistant.exceptions import HomeAssistantError

from .client_profile import OEM_VID, RTC_AES_PREFIX


class RtcMediaError(HomeAssistantError):
    """A safe-to-display RTC signaling or compatibility error."""


def derive_rtc_secret(account_session_id: str, magic_md5: int, cipher_text: str) -> str:
    """Port the app's source-backed DP9 key derivation."""
    if not account_session_id or not cipher_text:
        raise RtcMediaError("RTC bootstrap credentials are incomplete.")
    if magic_md5 < 0 or magic_md5 > 0xFFFFFFFF:
        raise RtcMediaError("RTC key derivation received an unsupported magic value.")
    key_hash = hashlib.md5((RTC_AES_PREFIX + account_session_id).encode()).hexdigest()
    bits = format(magic_md5, "032b")
    aes_key = "".join(char for bit, char in zip(bits, key_hash, strict=True) if bit == "1").encode()
    if len(aes_key) not in (16, 24, 32):
        raise RtcMediaError("RTC key derivation returned an unsupported key size.")
    try:
        raw = base64.b64decode(cipher_text, validate=True)
        if len(raw) <= 16:
            raise ValueError
        decryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).decryptor()
        padded = decryptor.update(raw[16:]) + decryptor.finalize()
        unpadder = PKCS7(algorithms.AES.block_size).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
    except (ValueError, TypeError) as err:
        raise RtcMediaError("RTC bootstrap key could not be derived.") from err
    return hashlib.md5(plain).hexdigest().upper()


def decode_nested_message(value: Any) -> dict[str, Any]:
    """Decode the protocol's JSON-string-valued ``message`` field."""
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        raise RtcMediaError("Camera returned an invalid RTC signaling message.")
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError) as err:
        raise RtcMediaError("Camera returned an invalid RTC signaling message.") from err
    if not isinstance(result, dict):
        raise RtcMediaError("Camera returned an invalid RTC signaling message.")
    return result


def media_sections(sdp: str) -> list[tuple[str, str]]:
    """Return (media kind, section text) pairs from an SDP document."""
    chunks = sdp.split("m=")
    sections: list[tuple[str, str]] = []
    for chunk in chunks[1:]:
        lines = chunk.splitlines()
        if not lines:
            continue
        kind = lines[0].split(maxsplit=1)[0].lower()
        sections.append((kind, chunk))
    return sections


def validate_browser_offer(sdp: str) -> None:
    """Require the camera's observed receive codecs before opening signaling.

    Real camera sessions in the checked-in diagnosis negotiated H.265 video
    and PCMU audio. A signaling proxy cannot transcode browser codecs, so it
    must reject an incompatible offer instead of returning a blank camera.
    """
    sections = media_sections(sdp)
    if not any(kind == "video" for kind, _ in sections):
        raise RtcMediaError("The browser offer does not include a video track.")
    if not any(kind == "audio" for kind, _ in sections):
        raise RtcMediaError("The browser offer does not include a receive-only audio track.")
    video_sections = [section for kind, section in sections if kind == "video"]
    audio_sections = [section for kind, section in sections if kind == "audio"]
    video = "\n".join(video_sections).lower()
    audio = "\n".join(audio_sections).lower()
    for kind, media in (("video", video_sections), ("audio", audio_sections)):
        for section in media:
            direction = next(
                (line[2:].strip().lower() for line in section.splitlines() if line in {
                    "a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive",
                }),
                "sendrecv",
            )
            if direction != "recvonly":
                raise RtcMediaError(
                    f"The browser offer must receive {kind} only; microphone and camera sending are disabled."
                )
    if "h265/90000" not in video and "hevc/90000" not in video:
        raise RtcMediaError(
            "This browser does not offer the camera's required H.265 video codec."
        )
    if "pcmu/8000" not in audio:
        raise RtcMediaError(
            "This browser does not offer the camera's required PCMU audio codec."
        )


def reorder_answer(offer_sdp: str, answer_sdp: str) -> str:
    """Put answer m-lines in offer order, as required by the camera SDP path."""
    offer = media_sections(offer_sdp)
    answer = media_sections(answer_sdp)
    offer_kinds = [kind for kind, _ in offer]
    answer_by_kind: dict[str, list[str]] = {}
    for kind, section in answer:
        answer_by_kind.setdefault(kind, []).append(section)
    if len(offer_kinds) != len(answer):
        return answer_sdp
    if any(len(answer_by_kind.get(kind, [])) != offer_kinds.count(kind) for kind in set(offer_kinds)):
        return answer_sdp
    ordered: list[str] = []
    for kind in offer_kinds:
        ordered.append(answer_by_kind[kind].pop(0))
    prefix = answer_sdp.split("m=", 1)[0]
    return prefix + "".join("m=" + section for section in ordered)


def build_signal(
    *,
    sid: int,
    serial: str,
    permit_code: str,
    message_type: str,
    payload: Any,
    call: bool = False,
) -> dict[str, Any]:
    """Build an IM CAM signal envelope without logging authorization data."""
    if not serial or not permit_code or sid < 0:
        raise RtcMediaError("RTC signaling authorization is incomplete.")
    envelope: dict[str, Any] = {
        "opcode": "message",
        "messageType": message_type,
        "sid": sid,
        "toSerialCode": serial,
        "permitCode": permit_code,
        "message": json.dumps(payload, separators=(",", ":")),
        "reqId": f"a_sig_{uuid.uuid4().hex}_1",
        "serialCode": "",
        "toSid": -1,
    }
    if call:
        envelope.update(callType=0, videoCodecType=0)
    return envelope


def candidate_payload(candidate: str, sdp_mid: str | None, mline_index: int) -> dict[str, Any]:
    """Normalize a browser ICE candidate for the remoteCandidate wire shape."""
    if not candidate or mline_index < 0:
        raise RtcMediaError("The browser supplied an invalid ICE candidate.")
    return {
        "candidate": candidate,
        "sdpMid": sdp_mid,
        "sdpMLineIndex": mline_index,
    }


def answer_from_envelope(envelope: Mapping[str, Any], offer_sdp: str) -> dict[str, str]:
    """Validate and normalize a callResponse payload without exposing SDP."""
    try:
        code = int(envelope.get("code", -1))
    except (TypeError, ValueError) as err:
        raise RtcMediaError("Camera rejected the live request.") from err
    if code != 0:
        raise RtcMediaError(f"Camera rejected the live request (code {code}).")
    answer = decode_nested_message(envelope.get("message"))
    sdp = answer.get("sdp")
    if answer.get("type") != "answer" or not isinstance(sdp, str) or not sdp:
        raise RtcMediaError("Camera returned an invalid SDP answer.")
    return {"type": "answer", "sdp": reorder_answer(offer_sdp, sdp)}


class ImCamRtcMedia:
    """Own ephemeral RTC signaling sessions for one integration entry."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._sessions: dict[str, ImCamRtcSession] = {}
        self._quality: dict[str, str] = {}
        self._ice_servers: list[RTCIceServer] = []
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._transcoded_tokens: dict[str, str] = {}
        self._configuration_lock = asyncio.Lock()
        self._ice_refresh_at = 0.0

    async def _bootstrap(self, control_session):
        reply = await control_session.request(
            "pub_dp_get", {"oem_vid": OEM_VID, "sn": "", "dpids": [9]}
        )
        rows = reply.get("body")
        row = next((item for item in rows if isinstance(item, dict) and item.get("id") == 9), None) if isinstance(rows, list) else None
        if row is None:
            raise RtcMediaError("IM CAM RTC configuration (DP9) is unavailable.")
        try:
            config = json.loads(row["value"])
            wss, access_id = config["wss"], config["access_id"]
            secret = derive_rtc_secret(control_session.session_id, int(config["magic_md5"]), config["aes_key"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as err:
            raise RtcMediaError("IM CAM RTC configuration is invalid.") from err
        if not isinstance(wss, str) or not wss.startswith("wss://") or not access_id:
            raise RtcMediaError("IM CAM RTC configuration is invalid.")

        devices = await control_session.request(
            "cli_dev_list_ungroup", {"time": 0, "type": 0, "page": 1, "num": 10000, "search": ""}
        )
        device_rows = (devices.get("body") or {}).get("list", [])
        permits = {
            item.get("sn"): item.get("webrtc_permit_code")
            for item in device_rows
            if isinstance(item, dict) and item.get("sn") and item.get("webrtc_permit_code")
        }
        return wss, access_id, secret, control_session.session_id, permits

    async def _sign_in(self, wss, access_id, secret, account_session_id):
        http = self.coordinator.api.http
        ws = await http.ws_connect(
            wss,
            headers={"Origin": wss.replace("wss://", "https://", 1)},
            heartbeat=20,
        )
        try:
            await ws.send_json({
                "opcode": "signin", "loginType": 0, "key": access_id,
                "secret": secret, "authType": 0, "authCode": "",
                "serialCode": account_session_id, "mac": "", "mustPermitCode": 0,
                "permitCode": "", "reUseSid": -1,
            })
            async with asyncio.timeout(20):
                while True:
                    frame = await ws.receive()
                    if frame.type.name != "TEXT":
                        if frame.type.name in {"ERROR", "CLOSE", "CLOSED", "CLOSING"}:
                            raise RtcMediaError("IM CAM RTC signaling connection closed during sign-in.")
                        continue
                    try:
                        response = json.loads(frame.data)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(response, dict) and response.get("opcode") == "signin":
                        break
            if int(response.get("code", -1)) != 0 or int(response.get("sid", -1)) < 0:
                raise RtcMediaError("IM CAM RTC signaling rejected sign-in.")
            stun = [RTCIceServer(url) for url in response.get("stunAddr", []) if url]
            turn = response.get("turnAddr", [])
            users = response.get("turnUser", [])
            passwords = response.get("turnPass", [])
            for index, url in enumerate(turn):
                if url:
                    stun.append(RTCIceServer(
                        url,
                        username=users[index] if index < len(users) else None,
                        credential=passwords[index] if index < len(passwords) else None,
                    ))
            return ws, int(response["sid"]), stun
        except BaseException:
            await ws.close()
            raise

    async def async_refresh_client_configuration(self):
        """Fetch current service ICE servers before HA asks the browser to offer."""
        if time.monotonic() - self._ice_refresh_at < 300:
            return
        async with self._configuration_lock:
            if time.monotonic() - self._ice_refresh_at < 300:
                return
            try:
                async with self.coordinator.async_control_session() as control:
                    wss, access, secret, account_sid, _ = await self._bootstrap(control)
                    ws, _sid, servers = await self._sign_in(wss, access, secret, account_sid)
                await ws.close()
                self._ice_servers = servers
                self._ice_refresh_at = time.monotonic()
            except Exception as err:
                raise HomeAssistantError("Unable to prepare IM CAM WebRTC configuration") from err

    def client_configuration(self):
        return WebRTCClientConfiguration(configuration=RTCConfiguration(ice_servers=list(self._ice_servers)))

    async def async_handle_offer(self, serial, offer_sdp, session_id, send_message):
        validate_browser_offer(offer_sdp)
        if session_id in self._sessions:
            self.close_session(session_id)
        control_stack = AsyncExitStack()
        try:
            control = await control_stack.enter_async_context(self.coordinator.async_control_session())
            wss, access, secret, account_sid, permits = await self._bootstrap(control)
            permit = permits.get(serial)
            if not permit:
                raise HomeAssistantError("Camera authorization for WebRTC is unavailable")
            # Signaling sign-in completes before the authenticated account socket
            # is transferred to the live session, preserving source client parity.
            ws, sid, servers = await self._sign_in(wss, access, secret, account_sid)
        except BaseException:
            await control_stack.aclose()
            raise
        if not self._ice_servers:
            self._ice_servers = servers
        session = ImCamRtcSession(
            self, ws, sid, serial, permit, session_id, offer_sdp, send_message, control_stack
        )
        self._sessions[session_id] = session
        try:
            await session.start()
        except BaseException:
            await self.async_close_session(session_id)
            raise

    async def async_on_candidate(self, session_id, candidate):
        session = self._sessions.get(session_id)
        if session is None:
            raise HomeAssistantError("IM CAM WebRTC session is no longer active")
        if not candidate.candidate:
            # HA forwards the browser's end-of-candidates event as an empty ICE
            # candidate. IM CAM's SDK protocol has no matching sentinel.
            return
        payload = candidate_payload(
            candidate.candidate,
            candidate.sdp_mid,
            candidate.sdp_m_line_index if candidate.sdp_m_line_index is not None else 0,
        )
        await session.send("remoteCandidate", payload)

    def close_session(self, session_id):
        session = self._sessions.pop(session_id, None)
        if session:
            task = self.coordinator.hass.async_create_task(session.async_close())
            self._cleanup_tasks.add(task)
            task.add_done_callback(self._cleanup_tasks.discard)

    async def async_close_session(self, session_id):
        session = self._sessions.pop(session_id, None)
        if session:
            await session.async_close()

    async def async_shutdown(self):
        """Close this entry's media sessions and await pending socket cleanup."""
        for session_id in tuple(self._sessions):
            await self.async_close_session(session_id)
        await asyncio.gather(
            *(self.async_close_transcoded_stream(serial) for serial in tuple(self._transcoded_tokens)),
            return_exceptions=True,
        )
        if self._cleanup_tasks:
            await asyncio.gather(*tuple(self._cleanup_tasks), return_exceptions=True)

    async def async_create_transcoded_stream(self, serial):
        """Start or reuse a server HLS session for a camera and return its source URL."""
        base_url = self.coordinator.transcoder_url
        api_key = self.coordinator.transcoder_api_key
        if not api_key:
            raise HomeAssistantError(
                "Set an API key for the IM CAM Transcoder app in the integration options."
            )
        try:
            async with self.coordinator.async_control_session() as control:
                wss, access, secret, account_sid, permits = await self._bootstrap(control)
            permit = permits.get(serial)
            if not permit:
                raise HomeAssistantError("Camera authorization for server transcoding is unavailable.")
            body = {
                "wss": wss,
                "accessId": access,
                "secret": secret,
                "sessionId": account_sid,
                "serial": serial,
                "permitCode": permit,
                "quality": self.quality(serial),
            }
            async with asyncio.timeout(65):
                response = await self.coordinator.api.http.post(
                    f"{base_url}/v1/streams",
                    json=body,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                result = await response.json()
            token = result.get("id") if isinstance(result, dict) else None
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,}", token):
                raise HomeAssistantError("The IM CAM Transcoder returned an invalid stream reference.")
            self._transcoded_tokens[serial] = token
            return f"{base_url}/hls/{token}/index.m3u8"
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError("Unable to start the IM CAM server transcoder.") from err

    async def async_close_transcoded_stream(self, serial):
        """Ask the companion app to stop one camera stream; idle cleanup is a fallback."""
        token = self._transcoded_tokens.pop(serial, None)
        if token is None or not self.coordinator.transcoder_api_key:
            return
        try:
            async with asyncio.timeout(5):
                response = await self.coordinator.api.http.delete(
                    f"{self.coordinator.transcoder_url}/v1/streams/{token}",
                    headers={"Authorization": f"Bearer {self.coordinator.transcoder_api_key}"},
                )
                response.raise_for_status()
        except Exception:
            # The sidecar also expires streams after playlist requests stop.
            pass

    async def async_set_quality(self, serial, value):
        if value not in {"SD", "HD"}:
            raise HomeAssistantError("Unknown IM CAM video quality")
        code = 2 if value == "HD" else 1
        for session in tuple(self._sessions.values()):
            if session.serial == serial and session.ready:
                await session.send("switchVideo", {"type": code, "act": 0})
        self._quality[serial] = value
        token = self._transcoded_tokens.get(serial)
        if token and self.coordinator.transcoder_api_key:
            try:
                async with asyncio.timeout(5):
                    response = await self.coordinator.api.http.post(
                        f"{self.coordinator.transcoder_url}/v1/streams/{token}/quality",
                        json={"quality": value},
                        headers={"Authorization": f"Bearer {self.coordinator.transcoder_api_key}"},
                    )
                    response.raise_for_status()
            except Exception as err:
                raise HomeAssistantError("Unable to change transcoded camera quality") from err

    def quality(self, serial):
        return self._quality.get(serial, "SD")


class ImCamRtcSession:
    """A camera signaling socket tied to one HA browser WebRTC session."""

    def __init__(self, owner, ws, sid, serial, permit, session_id, offer_sdp, send_message, control_stack=None):
        self.owner = owner
        self.ws = ws
        self.sid = sid
        self.serial = serial
        self.permit = permit
        self.session_id = session_id
        self.offer_sdp = offer_sdp
        self.send_message = send_message
        self.control_stack = control_stack
        self.answer_received = asyncio.Event()
        self.ready = False
        self.closed = False
        self.closing = False
        self._close_complete = asyncio.Event()
        self.reader_error = None
        self.reader = None
        self.heartbeat_task = None
        self._send_lock = asyncio.Lock()

    async def send(self, message_type, payload, *, call=False):
        if self.closed:
            raise RtcMediaError("IM CAM WebRTC signaling session is closed.")
        async with self._send_lock:
            await self.ws.send_json(build_signal(
                sid=self.sid, serial=self.serial, permit_code=self.permit,
                message_type=message_type, payload=payload, call=call,
            ))

    async def start(self):
        self.reader = asyncio.create_task(self._read_messages())
        self.heartbeat_task = asyncio.create_task(self._send_heartbeats())
        await self.send("callRequest", {"type": "offer", "sdp": self.offer_sdp}, call=True)
        try:
            async with asyncio.timeout(45):
                await self.answer_received.wait()
            if self.reader_error:
                raise HomeAssistantError("Camera could not complete the WebRTC negotiation") from self.reader_error
            await self.send("switchAudio", {"type": 1, "act": 0})
            quality = self.owner.quality(self.serial)
            await self.send("switchVideo", {"type": 2 if quality == "HD" else 1, "act": 0})
            self.ready = True
        except TimeoutError as err:
            await self.async_close()
            raise HomeAssistantError("Camera did not answer the WebRTC offer in time") from err
        except asyncio.CancelledError:
            await asyncio.shield(self.async_close())
            raise
        except Exception:
            await self.async_close()
            raise

    async def _read_messages(self):
        try:
            while not self.closed:
                frame = await self.ws.receive()
                if frame.type.name in {"ERROR", "CLOSE", "CLOSED", "CLOSING"}:
                    raise RtcMediaError("IM CAM RTC signaling connection closed.")
                if frame.type.name != "TEXT":
                    continue
                try:
                    envelope = json.loads(frame.data)
                except (ValueError, TypeError):
                    continue
                if not isinstance(envelope, dict):
                    continue
                kind = envelope.get("messageType")
                if kind == "callResponse":
                    answer = answer_from_envelope(envelope, self.offer_sdp)
                    self.send_message(WebRTCAnswer(answer["sdp"]))
                    self.answer_received.set()
                elif kind == "remoteCandidate":
                    payload = decode_nested_message(envelope.get("message"))
                    candidate = payload.get("candidate")
                    if candidate:
                        self.send_message(WebRTCCandidate(RTCIceCandidateInit(
                            candidate=candidate,
                            sdp_mid=payload.get("sdpMid"),
                            sdp_m_line_index=int(payload.get("sdpMLineIndex", 0)),
                        )))
                elif kind == "hangup":
                    raise RtcMediaError("Camera ended the WebRTC session.")
        except asyncio.CancelledError:
            raise
        except Exception as err:
            if not self.closed and not self.closing:
                self.reader_error = err
                if self.ready:
                    self.send_message(WebRTCError(
                        "im_cam_signaling_failed",
                        str(err) if isinstance(err, RtcMediaError) else "Camera signaling stopped unexpectedly.",
                    ))
                self.answer_received.set()
                await self.owner.async_close_session(self.session_id)

    async def _send_heartbeats(self):
        try:
            while not self.closed and not self.closing:
                await asyncio.sleep(5)
                if not self.closed and not self.closing:
                    await self.ws.send_json({"opcode": "heartbeat", "reqId": "a_sig_1"})
        except asyncio.CancelledError:
            raise
        except Exception as err:
            if not self.closed and not self.closing:
                self.reader_error = err
                if self.ready:
                    self.send_message(WebRTCError(
                        "im_cam_signaling_failed", "Camera signaling stopped unexpectedly."
                    ))
                self.answer_received.set()
                await self.owner.async_close_session(self.session_id)

    async def async_close(self):
        if self.closed:
            return
        if self.closing:
            await self._close_complete.wait()
            return
        self.closing = True
        current_task = asyncio.current_task()
        to_cancel = [
            task for task in (self.reader, self.heartbeat_task)
            if task is not None and task is not current_task and not task.done()
        ]
        for task in to_cancel:
            task.cancel()
        if to_cancel:
            await asyncio.gather(*to_cancel, return_exceptions=True)
        try:
            if not self.ws.closed:
                # Send hangup before closing the socket. Cleanup remains best effort.
                with suppress(Exception):
                    async with self._send_lock:
                        async with asyncio.timeout(3):
                            await self.ws.send_json(build_signal(
                                sid=self.sid, serial=self.serial, permit_code=self.permit,
                                message_type="hangup", payload="",
                            ))
                with suppress(Exception):
                    async with asyncio.timeout(3):
                        await self.ws.close()
        finally:
            try:
                if self.control_stack is not None:
                    async with asyncio.timeout(5):
                        await self.control_stack.aclose()
            finally:
                self.closed = True
                self.closing = False
                self._close_complete.set()
