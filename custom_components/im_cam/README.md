# IM CAM for Home Assistant

[![Open your Home Assistant instance and open IM CAM in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=iliasgws&repository=im-cam-home-assistant&category=integration)

<p align="center"><img src="https://raw.githubusercontent.com/iliasgws/im-cam-home-assistant/main/custom_components/im_cam/brand/icon@2x.png" width="128" alt="IM CAM"></p>

This standalone integration connects directly to IM CAM with your account and password. It adds camera controls and a native WebRTC live-view path without the IM-CAM CLI or Java. An optional Home Assistant OS app can decode and transcode video on the server. The integration ships its own icon and logo; Home Assistant 2026.3 and later display them from the bundled `brand/` images.

## Requirements

- Home Assistant Core with custom integrations enabled.
- The account must have linked cameras.

## Install

Install with the **Open in HACS** badge above, then **restart Home Assistant** before adding **IM CAM** under Settings → Devices & services. Enter your IM CAM account and password; client registration is bundled with the integration. The integration polls every two minutes.

The HACS repository is **iliasgws/im-cam-home-assistant** on GitHub. The GitLab project holds the Android app and a copy of this integration.

Your account password is stored in the Home Assistant config entry and hashed in memory when constructing the login request. The client registration profile is shared by users of this integration. Keep a backup of the Home Assistant configuration private.

## Server-side video transcoding (Home Assistant OS)

The optional **IM CAM Transcoder** app creates a second camera entity and converts the camera's H.265 video to H.264 on the Home Assistant host. This uses software encoding and can increase CPU load. It outputs video only; the original **Camera** entity remains available for direct WebRTC playback with camera audio.

To try beta **0.4.0-beta.5**, install that prerelease of the integration through HACS and add the feature branch as the Apps repository. The branch-specific app hostname is `d4719374-imcam-transcoder`.

1. In **Settings → Apps**, open the three-dot menu, choose **Repositories**, and add `https://github.com/iliasgws/im-cam-home-assistant#feat/server-side-transcoding`.
2. Install **IM CAM Transcoder**. In its configuration, set a random API key of at least 32 characters.
3. In the IM CAM integration's options, enable **Server transcoding**, set the app URL to `http://d4719374-imcam-transcoder:8787`, and enter the same API key.
4. Reload the integration and use the new **Server transcoded camera** entity.

The app serves H.264 HLS at 640×360 for SD or 1280×720 for HD, both at up to 15 fps. HD means 720p output. The server allows two concurrent camera pipelines and stops a stream after it becomes idle. The transcoded HLS path has more latency than direct WebRTC.

## Update an existing installation

1. Open **IM CAM** in HACS and choose **Update information** from its menu.
2. Install version **0.3.2** or newer. If HACS does not offer an update for an earlier commit-based installation, choose **Redownload**, select **0.3.2**, and download it.
3. Close any open IM CAM setup dialog and **restart Home Assistant itself**. Reloading the integration or refreshing the browser does not replace its loaded Python config flow.
4. Open IM CAM setup again. It should ask only for **Account** and **Password**.

Existing IM CAM config entries can stay in place; their account and password still work with the bundled client profile. You do not need to delete your devices or entities to update.

If the old seven-field form remains after a restart, check that HACS lists **iliasgws/im-cam-home-assistant**. In Home Assistant's file editor, `custom_components/im_cam/manifest.json` should show version `0.3.2`, and `config_flow.py` should have only `CONF_ACCOUNT` and `CONF_PASSWORD` in its form schema. This distinguishes an old download from code still loaded in memory.

## Entities

Each camera's device page has:

- **Connection** sensor: the service's reported online/offline state, or unknown when it omits that information.
- **Privacy mode** switch: reads and writes the camera's privacy setting. On means privacy is enabled. A physical shutter moves only on models that implement one. The switch is unavailable when the camera does not report a valid privacy value.
- **Pan/tilt Up, Down, Left, Right, Stop** buttons: directional presses send a 500 ms pulse and then stop. The stop button sends a separate stop command. Network failure can prevent stop delivery; this is not a hardware movement limit. Cameras without PTZ hardware may ignore these commands.
- **Camera** entity: receive-only live video and camera audio through Home Assistant's native WebRTC player. Unmute the player to listen. The integration never captures your microphone.
- **Video quality** selector: SD or HD for active live sessions and the next session. Selecting HD without an active stream stores a preference; it does not confirm a camera resolution change.

Enable privacy to stop viewing. Disable privacy and reopen the player to start a fresh session. PTZ and playback are unavailable while privacy is reported as on.

## Live video and audio compatibility

The tested camera protocol uses H.265 video and PCMU audio. A direct WebRTC bridge cannot transcode those codecs. If your browser does not offer them, the player reports a codec compatibility error. Browser support varies by platform and hardware; the presence of a Camera entity does not establish that your browser can decode its feed.

The direct WebRTC path uses authenticated account/device authorization, service-provided STUN/TURN settings, and ephemeral signaling sessions. Session secrets and camera permits are kept in memory and are not entity attributes. Two-way talk and snapshot images are not implemented.

The existing controls and direct signaling are covered by mocked protocol/lifecycle checks. The transcoder app and HLS camera path were validated on an amd64 Home Assistant OS install with Home Assistant Core 2026.8.2 and a real camera: browser playback worked at 640×360 SD and switched live to 1280×720 HD. The existing Linux CLI's camera verification does not establish behavior on every camera, HAOS device, or network.

The native asynchronous WebRTC camera API used here requires Home Assistant **2024.12.0 or newer**.
