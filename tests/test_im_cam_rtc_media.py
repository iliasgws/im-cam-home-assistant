"""Mocked wire and lifecycle tests for IM CAM's receive-only camera bridge."""

import asyncio
import json
import unittest

from homeassistant.exceptions import HomeAssistantError

from custom_components.im_cam.rtc_media import (
    ImCamRtcMedia,
    ImCamRtcSession,
    RtcMediaError,
    answer_from_envelope,
    build_signal,
    candidate_payload,
    validate_browser_offer,
)


class RtcMediaProtocolTests(unittest.TestCase):
    def test_call_envelope_uses_nested_json_and_call_fields(self):
        signal = build_signal(
            sid=12,
            serial="camera-1",
            permit_code="permit-value",
            message_type="callRequest",
            payload={"type": "offer", "sdp": "safe-test-sdp"},
            call=True,
        )
        self.assertEqual(signal["opcode"], "message")
        self.assertEqual(signal["messageType"], "callRequest")
        self.assertEqual(json.loads(signal["message"]), {"type": "offer", "sdp": "safe-test-sdp"})
        self.assertEqual((signal["callType"], signal["videoCodecType"]), (0, 0))
        self.assertEqual(signal["serialCode"], "")
        self.assertEqual(signal["toSid"], -1)
        self.assertTrue(signal["reqId"].startswith("a_sig_"))

    def test_incomplete_authorization_is_rejected(self):
        with self.assertRaisesRegex(RtcMediaError, "authorization is incomplete"):
            build_signal(sid=-1, serial="camera-1", permit_code="", message_type="hangup", payload="")

    def test_browser_codec_gate_requires_observed_audio_and_video(self):
        offer = """v=0
m=video 9 UDP/TLS/RTP/SAVPF 98
a=rtpmap:98 H265/90000
a=recvonly
m=audio 9 UDP/TLS/RTP/SAVPF 0
a=rtpmap:0 PCMU/8000
a=recvonly
"""
        validate_browser_offer(offer)
        with self.assertRaisesRegex(RtcMediaError, "does not offer.*H.265"):
            validate_browser_offer(offer.replace("H265/90000", "VP8/90000"))

    def test_answer_media_sections_follow_offer_order(self):
        offer = "v=0\r\nm=video 9 RTP/AVP 98\r\na=recvonly\r\nm=audio 9 RTP/AVP 0\r\na=recvonly\r\n"
        answer = "v=0\r\nm=audio 9 RTP/AVP 0\r\nm=video 9 RTP/AVP 98\r\n"
        normalized = answer_from_envelope(
            {"code": 0, "message": json.dumps({"type": "answer", "sdp": answer})}, offer
        )
        self.assertEqual(normalized["type"], "answer")
        self.assertLess(normalized["sdp"].index("m=video"), normalized["sdp"].index("m=audio"))

    def test_candidate_wire_payload(self):
        self.assertEqual(
            candidate_payload("candidate:1 1 UDP 1 192.0.2.1 9 typ host", "0", 0),
            {"candidate": "candidate:1 1 UDP 1 192.0.2.1 9 typ host", "sdpMid": "0", "sdpMLineIndex": 0},
        )


class _Frame:
    def __init__(self, data):
        self.type = type("FrameType", (), {"name": "TEXT"})()
        self.data = data


class _MockSocket:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()
        self.closed = False

    async def send_json(self, message):
        self.sent.append(message)

    async def receive(self):
        return await self.incoming.get()

    async def close(self):
        self.closed = True


class _MockHass:
    def async_create_task(self, coroutine):
        return asyncio.create_task(coroutine)


class _MockOwner:
    def __init__(self):
        self.coordinator = type("Coordinator", (), {"hass": _MockHass()})()
        self.sent_messages = []

    def quality(self, _serial):
        return "SD"

    async def async_close_session(self, _session_id):
        await self.session.async_close()


class RtcMediaLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def _session(self):
        owner = _MockOwner()
        ws = _MockSocket()
        offer = "v=0\r\nm=video 9 RTP/AVP 98\r\na=recvonly\r\nm=audio 9 RTP/AVP 0\r\na=recvonly\r\n"
        session = ImCamRtcSession(
            owner, ws, 3, "camera-1", "private-permit", "ha-session", offer,
            owner.sent_messages.append,
        )
        owner.session = session
        return owner, ws, session, offer

    async def test_offer_answer_audio_video_and_hangup_lifecycle(self):
        owner, ws, session, offer = await self._session()
        started = asyncio.create_task(session.start())
        await asyncio.sleep(0)
        self.assertEqual(ws.sent[0]["messageType"], "callRequest")
        await ws.incoming.put(_Frame(json.dumps({
            "messageType": "callResponse", "code": 0,
            "message": json.dumps({"type": "answer", "sdp": offer}),
        })))
        await started

        self.assertTrue(session.ready)
        self.assertEqual(
            [message["messageType"] for message in ws.sent],
            ["callRequest", "switchAudio", "switchVideo"],
        )
        self.assertEqual(owner.sent_messages[0].answer, offer)

        await session.async_close()
        self.assertTrue(ws.closed)
        self.assertEqual(ws.sent[-1]["messageType"], "hangup")
        self.assertTrue(session.closed)

    async def test_rejected_call_never_enables_media(self):
        _owner, ws, session, _offer = await self._session()
        started = asyncio.create_task(session.start())
        await asyncio.sleep(0)
        await ws.incoming.put(_Frame(json.dumps({"messageType": "callResponse", "code": 13, "message": "{}"})))

        with self.assertRaises(HomeAssistantError):
            await started
        self.assertEqual([message["messageType"] for message in ws.sent], ["callRequest", "hangup"])
        self.assertFalse(session.ready)

    async def test_quality_does_not_change_when_active_camera_rejects_send(self):
        media = ImCamRtcMedia(type("Coordinator", (), {})())
        session = type("Session", (), {"serial": "camera-1", "ready": True})()

        async def reject(_message_type, _payload):
            raise HomeAssistantError("mock send failure")

        session.send = reject
        media._sessions["ha-session"] = session
        with self.assertRaises(HomeAssistantError):
            await media.async_set_quality("camera-1", "HD")
        self.assertEqual(media.quality("camera-1"), "SD")


if __name__ == "__main__":
    unittest.main()
