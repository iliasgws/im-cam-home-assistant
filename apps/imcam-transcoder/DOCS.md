# IM CAM Transcoder

This Home Assistant OS app receives a camera's H.265 WebRTC feed and converts it
to H.264 HLS for Home Assistant's stream integration. It is used by the
integration's separate **Server transcoded camera** entity.

The app runs its own Debian-based container with GStreamer, Python GI bindings,
and the WebRTC/HLS plugins. It does not install media packages into Home
Assistant Core or modify the Home Assistant OS host.

## Configure

1. Choose a random API key with at least 32 characters and save it in the app's
   `api_key` option.
2. In the IM CAM integration options, enable server transcoding and enter the
   same key. The default app URL is the internal Supervisor-network name for
   this app when installed from the published repository.
3. Reload the integration. The **Server transcoded camera** entity appears for
   each camera.

The API listens only on the app network; no host port is published. HLS playlist
and segment paths contain a random per-session token and do not carry IM CAM
authorization data. The app does not log HTTP request paths or camera
credentials. It keeps credentials in memory and removes HLS files when the stream
is stopped or becomes idle.

## Encoding and limits

- SD: H.264 640×360, up to 15 fps, approximately 650 kbit/s.
- HD: H.264 1280×720, up to 15 fps, approximately 2200 kbit/s.
- Video only; audio remains on the original direct WebRTC entity.
- Up to two concurrent camera pipelines.
- Streams stop when HLS clients stop requesting playlist/segments.

Encoding uses the CPU. Higher resolutions and multiple streams increase load.
