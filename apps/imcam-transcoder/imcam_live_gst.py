"""Receive-only Linux viewer using GStreamer / libnice for camera compatibility."""

import asyncio
import json
import os
import signal
import sys
import threading
import traceback
import uuid
from urllib.parse import quote

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
from gi.repository import GLib, Gst, GstSdp, GstWebRTC
import websockets

FIRST_FRAME_TIMEOUT_SECONDS = 45
REQUIRED_ELEMENTS = ("webrtcbin", "nicesrc", "dtlssrtpdec", "rtph265depay", "avdec_h265", "videoconvert", "autovideosink")


class LiveViewError(Exception):
    pass


def say(message):
    safe_message = "".join(c if c.isprintable() else " " for c in str(message))[:500]
    print("IMCAM: " + safe_message, file=sys.stderr, flush=True)


def check_runtime(server_hls=False):
    Gst.init(None)
    required = REQUIRED_ELEMENTS
    if server_hls:
        required = (
            "webrtcbin", "nicesrc", "dtlssrtpdec", "rtph265depay", "avdec_h265",
            "videoconvert", "videoscale", "videorate", "x264enc", "h264parse", "mpegtsmux", "hlssink",
        )
    missing = [name for name in required if not Gst.ElementFactory.find(name)]
    if missing:
        purpose = "server transcoding" if server_hls else "the Linux viewer"
        raise LiveViewError("Missing GStreamer plugins for " + purpose + ": " + ", ".join(missing) + ".")


def build_message(sid, data, kind, payload, call=False):
    message = {
        "opcode": "message", "messageType": kind, "sid": sid,
        "toSerialCode": data["serial"], "permitCode": data["permitCode"],
        "message": json.dumps(payload, separators=(",", ":")),
        "reqId": f"a_sig_{uuid.uuid4()}_1", "serialCode": "", "toSid": -1,
    }
    if call:
        message.update(callType=0, videoCodecType=0)
    return json.dumps(message, separators=(",", ":"))


def reorder_answer(offer, answer):
    # Camera answers can put audio first, regardless of the offer's order.
    local = [part.splitlines()[0].split()[0] for part in offer.split("m=")[1:]]
    sections = answer.split("m=")
    media = {part.splitlines()[0].split()[0]: part for part in sections[1:]}
    if len(media) == len(local) and all(kind in media for kind in local):
        return sections[0] + "".join("m=" + media[kind] for kind in local)
    return answer


async def run(data):
    server_hls = bool(data.get("hls_dir"))
    check_runtime(server_hls)
    if server_hls:
        os.makedirs(data["hls_dir"], mode=0o700, exist_ok=True)
    if data.get("audio"):
        missing = [name for name in ("rtppcmudepay", "mulawdec", "autoaudiosink") if not Gst.ElementFactory.find(name)]
        if missing:
            raise LiveViewError("Missing GStreamer audio plugins: " + ", ".join(missing))
    loop = asyncio.get_running_loop()
    controls_queue = asyncio.Queue()

    async def read_controls():
        # A blocking executor readline survives task cancellation and prevents
        # asyncio.run from reporting negotiation errors until stdin closes.
        reader = asyncio.StreamReader()
        transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
        try:
            while line := await reader.readline():
                try:
                    command = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(command, dict):
                    await controls_queue.put(command)
        finally:
            transport.close()

    control_reader = asyncio.create_task(read_controls()) if server_hls else None
    main = GLib.MainLoop()
    thread = threading.Thread(target=main.run, daemon=True)
    thread.start()
    pipeline = Gst.Pipeline.new("imcam")
    rtc = Gst.ElementFactory.make("webrtcbin", "rtc")
    rtc.set_property("bundle-policy", GstWebRTC.WebRTCBundlePolicy.MAX_BUNDLE)
    pipeline.add(rtc)
    candidates = asyncio.Queue()
    errors = asyncio.Queue()
    stopped = asyncio.Event()
    first_frame = asyncio.Event()
    frame_count = [0]
    last_dimensions = [None]
    audio_received = [False]
    transcoder_elements = {}
    quality_state = [int(data.get("quality_code", data.get("quality", 1)))]

    async def wait_promise(promise):
        # A plain to_thread(wait) can strand an executor thread indefinitely.
        waiter = asyncio.create_task(asyncio.to_thread(promise.wait))
        stop = asyncio.create_task(stopped.wait())
        try:
            done, _ = await asyncio.wait((waiter, stop), timeout=15, return_when=asyncio.FIRST_COMPLETED)
            if stop in done:
                raise asyncio.CancelledError
            if waiter not in done:
                raise LiveViewError("WebRTC negotiation timed out.")
            if waiter.result() != Gst.PromiseResult.REPLIED:
                raise LiveViewError("WebRTC negotiation did not complete.")
            reply = promise.get_reply()
            if reply is not None and reply.has_field("error"):
                # Do not print GError details, which may contain remote SDP.
                raise LiveViewError("WebRTC negotiation failed.")
        finally:
            promise.interrupt()
            stop.cancel()
            await asyncio.gather(waiter, stop, return_exceptions=True)

    def on_candidate(_, index, candidate):
        loop.call_soon_threadsafe(candidates.put_nowait, (index, candidate))

    def on_frame(sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.EOS
        frame_count[0] += 1
        # Gst.Structure is borrowed from Caps: retain both wrappers.
        caps = sample.get_caps()
        structure = caps.get_structure(0)
        width, height = structure.get_value("width"), structure.get_value("height")
        if frame_count[0] == 1:
            loop.call_soon_threadsafe(first_frame.set)
            say(f"Video frames received ({width}x{height}). Close the video window or press Ctrl-C to stop.")
        elif last_dimensions[0] != (width, height):
            say(f"Video resolution changed to {width}x{height}.")
        last_dimensions[0] = (width, height)
        return Gst.FlowReturn.OK

    def on_server_frame(_identity, _buffer):
        if not first_frame.is_set():
            loop.call_soon_threadsafe(first_frame.set)

    def on_audio(_identity, _buffer):
        if not audio_received[0]:
            audio_received[0] = True
            say("Camera audio received; playing through the default audio output.")

    def on_pad(_, pad):
        try:
            caps = pad.get_current_caps()
            if caps is None:
                raise LiveViewError("Incoming media has no format.")
            media = caps.get_structure(0).get_string("media")
            if media == "video":
                codec = caps.get_structure(0).get_string("encoding-name")
                if codec != "H265":
                    raise LiveViewError("Camera returned an unsupported video codec.")
                if server_hls:
                    hls_dir = data["hls_dir"]
                    initial_quality = quality_state[0]
                    width, height, bitrate = (1280, 720, 2200) if initial_quality == 2 else (640, 360, 650)
                    branch = Gst.parse_bin_from_description(
                        "queue ! rtph265depay ! avdec_h265 ! videoconvert ! videoscale ! videorate ! "
                        f'capsfilter name=outputcaps caps="video/x-raw,width={width},height={height},framerate=15/1,format=I420" ! '
                        "identity name=serverframes signal-handoffs=true ! "
                        f"x264enc name=transcode tune=zerolatency speed-preset=ultrafast bitrate={bitrate} "
                        "threads=2 key-int-max=30 bframes=0 ! h264parse config-interval=-1 ! "
                        "mpegtsmux ! hlssink "
                        f"location={hls_dir}/segment%05d.ts "
                        f"playlist-location={hls_dir}/index.m3u8 "
                        "target-duration=2 playlist-length=4 max-files=6 sync=false", True,
                    )
                    transcoder_elements["caps"] = branch.get_by_name("outputcaps")
                    transcoder_elements["encoder"] = branch.get_by_name("transcode")
                    branch.get_by_name("serverframes").connect("handoff", on_server_frame)
                else:
                    sink = "fakesink" if os.environ.get("IMCAM_LIVE_HEADLESS") == "1" else "autovideosink"
                    # Preserve the verified allocation/format negotiation: forcing
                    # RGB directly into the display sink produced black/green output
                    # on the test host, despite valid decoded camera frames.
                    branch = Gst.parse_bin_from_description(
                        "queue ! rtph265depay ! avdec_h265 ! videoconvert ! tee name=t "
                        f"t. ! queue ! {sink} sync=false "
                        "t. ! queue ! video/x-raw,format=RGB ! appsink name=frames emit-signals=true sync=false", True,
                    )
                    branch.get_by_name("frames").connect("new-sample", on_frame)
            elif media == "audio" and data.get("audio"):
                branch = Gst.parse_bin_from_description(
                    "queue ! rtppcmudepay ! mulawdec ! audioconvert ! audioresample ! "
                    "identity name=audioframes signal-handoffs=true ! autoaudiosink sync=false", True,
                )
                branch.get_by_name("audioframes").connect("handoff", on_audio)
            else:
                # Negotiate audio for protocol compatibility; never play or save it.
                branch = Gst.parse_bin_from_description("queue ! fakesink sync=false", True)
            pipeline.add(branch)
            branch.sync_state_with_parent()
            if pad.link(branch.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
                raise LiveViewError("Could not connect incoming media to the viewer.")
        except Exception as error:
            if isinstance(error, LiveViewError):
                message = str(error)
            else:
                location = traceback.extract_tb(error.__traceback__)[-1]
                attribute = f", missing {error.name}" if isinstance(error, AttributeError) else ""
                message = f"Video setup failed ({type(error).__name__}{attribute}, line {location.lineno})."
            loop.call_soon_threadsafe(errors.put_nowait, message)

    rtc.connect("on-ice-candidate", on_candidate)
    rtc.connect("pad-added", on_pad)
    for media, codec, payload, rate in [("video", "H265", 125, 90000), ("audio", "PCMU", 0, 8000)]:
        caps = Gst.Caps.from_string(f"application/x-rtp,media={media},encoding-name={codec},payload={payload},clock-rate={rate}")
        rtc.emit("add-transceiver", GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY, caps)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    pipeline.set_state(Gst.State.PLAYING)
    bus = pipeline.get_bus()
    try:
        async with websockets.connect(
            data["wss"], origin=data["wss"].replace("wss://", "https://"),
            compression=None, user_agent_header="okhttp/4.12.0", open_timeout=30,
        ) as ws:
            await ws.send(json.dumps({
                "opcode": "signin", "loginType": 0, "key": data["accessId"], "secret": data["secret"],
                "authType": 0, "authCode": "", "serialCode": data["sessionId"], "mac": "",
                "mustPermitCode": 0, "permitCode": "", "reUseSid": -1,
            }))
            async with asyncio.timeout(20):
                while True:
                    response = json.loads(await ws.recv())
                    if response.get("opcode") == "signin":
                        break
            if int(response.get("code", -1)) != 0 or int(response.get("sid", -1)) < 0:
                raise LiveViewError("RTC signaling rejected sign-in.")
            sid = int(response["sid"])
            for url in response.get("stunAddr", [])[:1]:
                if url:
                    rtc.set_property("stun-server", url)
            for index, url in enumerate(response.get("turnAddr", [])):
                users, passwords = response.get("turnUser", []), response.get("turnPass", [])
                if url and index < len(users) and index < len(passwords):
                    scheme, separator, address = url.partition("://")
                    if separator:
                        rtc.emit("add-turn-server", f"{scheme}://{quote(users[index], safe='')}:{quote(passwords[index], safe='')}@{address}")

            tasks = []
            controls = None
            try:
                promise = Gst.Promise.new()
                rtc.emit("create-offer", None, promise)
                await wait_promise(promise)
                reply = promise.get_reply()
                if reply is None or not reply.has_field("offer"):
                    raise LiveViewError("Could not create a WebRTC offer.")
                # Keep/copy boxed GI values before releasing their owning promise.
                offer = reply.get_value("offer").copy()
                offer_sdp = offer.sdp.as_text()
                mids = [line[6:] for line in offer_sdp.splitlines() if line.startswith("a=mid:")]
                promise = Gst.Promise.new()
                rtc.emit("set-local-description", offer, promise)
                await wait_promise(promise)
                await ws.send(build_message(sid, data, "callRequest", {"type": "offer", "sdp": offer_sdp}, call=True))
                say("Camera call requested; waiting for video…")

                async def send_candidates():
                    while True:
                        index, candidate = await candidates.get()
                        await ws.send(build_message(sid, data, "remoteCandidate", {
                            "candidate": candidate, "sdpMid": mids[index], "sdpMLineIndex": index,
                        }))

                async def heartbeat():
                    while True:
                        await asyncio.sleep(5)
                        await ws.send(json.dumps({"opcode": "heartbeat", "reqId": "a_sig_1"}))

                async def configure_media():
                    # Apply camera controls after its live sender has started.
                    await first_frame.wait()
                    quality = quality_state[0]
                    if quality in (1, 2):
                        await ws.send(build_message(sid, data, "switchVideo", {"type": quality, "act": 0}))
                        say("Requested " + ("HD" if quality == 2 else "SD") + " camera video.")
                    if data.get("audio"):
                        await ws.send(build_message(sid, data, "switchAudio", {"type": 1, "act": 0}))
                        say("Camera audio playback enabled.")

                tasks = [asyncio.create_task(send_candidates()), asyncio.create_task(heartbeat())]
                controls = asyncio.create_task(configure_media())
                deadline = loop.time() + FIRST_FRAME_TIMEOUT_SECONDS
                while not stopped.is_set():
                    while not controls_queue.empty():
                        command = controls_queue.get_nowait()
                        if command.get("type") == "quality" and command.get("quality") in {"SD", "HD"}:
                            quality = 2 if command["quality"] == "HD" else 1
                            quality_state[0] = quality
                            if server_hls and transcoder_elements:
                                width, height, bitrate = (1280, 720, 2200) if quality == 2 else (640, 360, 650)
                                transcoder_elements["caps"].set_property(
                                    "caps",
                                    Gst.Caps.from_string(
                                        f"video/x-raw,width={width},height={height},framerate=15/1,format=I420"
                                    ),
                                )
                                transcoder_elements["encoder"].set_property("bitrate", bitrate)
                            await ws.send(build_message(sid, data, "switchVideo", {"type": quality, "act": 0}))
                    if not errors.empty():
                        raise LiveViewError(errors.get_nowait())
                    for task in tasks:
                        if task.done():
                            task.result()
                    if controls.done():
                        controls.result()
                    message = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
                    if message:
                        if message.type == Gst.MessageType.EOS:
                            break
                        error, _debug = message.parse_error()
                        # Video sinks commonly report window closure as a resource error.
                        window_error = (error.message + " " + (_debug or "")).lower()
                        if (error.matches(Gst.ResourceError.quark(), Gst.ResourceError.NOT_FOUND)
                                and (("window" in window_error and "clos" in window_error)
                                     or error.message.lower() == "quit requested")):
                            break
                        # GStreamer debug strings may contain SDP/credentials.
                        raise LiveViewError(f"Video pipeline failed ({error.domain}, code {error.code}).")
                    if not first_frame.is_set() and loop.time() >= deadline:
                        raise LiveViewError(f"No video frame arrived within {FIRST_FRAME_TIMEOUT_SECONDS} seconds.")
                    try:
                        envelope = json.loads(await asyncio.wait_for(ws.recv(), 0.25))
                    except asyncio.TimeoutError:
                        continue
                    kind = envelope.get("messageType")
                    if kind == "callResponse":
                        code = int(envelope.get("code", -1))
                        if code != 0:
                            raise LiveViewError(f"Camera rejected the live request (code {code}).")
                        answer = json.loads(envelope.get("message") or "{}")
                        if answer.get("type") != "answer" or not answer.get("sdp"):
                            raise LiveViewError("Camera returned an invalid SDP answer.")
                        result, sdp = GstSdp.SDPMessage.new_from_text(reorder_answer(offer_sdp, answer["sdp"]))
                        if result != GstSdp.SDPResult.OK:
                            raise LiveViewError("Camera returned an invalid SDP answer.")
                        description = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdp)
                        promise = Gst.Promise.new()
                        rtc.emit("set-remote-description", description, promise)
                        await wait_promise(promise)
                    elif kind == "remoteCandidate":
                        payload = json.loads(envelope.get("message") or "{}")
                        if payload.get("candidate"):
                            rtc.emit("add-ice-candidate", int(payload.get("sdpMLineIndex", 0)), payload["candidate"])
                    elif kind == "hangup":
                        raise LiveViewError("Camera ended the live session.")
            finally:
                if controls is not None:
                    controls.cancel()
                    await asyncio.gather(controls, return_exceptions=True)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await ws.send(build_message(sid, data, "hangup", ""))
                except Exception:
                    pass
    finally:
        if control_reader is not None:
            control_reader.cancel()
            await asyncio.gather(control_reader, return_exceptions=True)
        pipeline.set_state(Gst.State.NULL)
        main.quit()
        await asyncio.to_thread(thread.join, 3)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        if frame_count[0]:
            say(f"Live view stopped ({frame_count[0]} decoded frames).")


def main():
    try:
        if sys.argv[1:] == ["--check"]:
            check_runtime()
            return
        raw = sys.stdin.readline()
        if not raw:
            raise LiveViewError("Live-view bootstrap data was not provided.")
        asyncio.run(run(json.loads(raw)))
    except (KeyboardInterrupt, asyncio.CancelledError):
        say("Stopping live view…")
    except LiveViewError as error:
        say(str(error))
        raise SystemExit(1)
    except Exception as error:
        say(f"Live view failed ({type(error).__name__}).")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
