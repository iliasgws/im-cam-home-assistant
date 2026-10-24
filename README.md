# IM CAM for Home Assistant

[![Open your Home Assistant instance and open IM CAM in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=iliasgws&repository=im-cam-home-assistant&category=integration)

This standalone integration connects directly to IM CAM with your account and password. It adds camera controls and a native WebRTC live-view path without the IM-CAM CLI, Java, or GStreamer. Live video/audio support is experimental and requires compatible browser codecs.

## Requirements

- Home Assistant Core with custom integrations enabled.
- The account must have linked cameras.

## Install

Install with the **Open in HACS** badge above, then **restart Home Assistant** before adding **IM CAM** under Settings → Devices & services. Enter your IM CAM account and password; client registration is bundled with the integration. The integration polls every two minutes.

The HACS repository is **iliasgws/im-cam-home-assistant** on GitHub. The GitLab project holds the Android app and a copy of this integration.

Your account password is stored in the Home Assistant config entry and hashed in memory when constructing the login request. The client registration profile is shared by users of this integration. Keep a backup of the Home Assistant configuration private.

## Update an existing installation

1. Open **IM CAM** in HACS and choose **Update information** from its menu.
2. Install version **0.3.0** or newer. If HACS does not offer an update for an earlier commit-based installation, choose **Redownload**, select **0.3.0**, and download it.
3. Close any open IM CAM setup dialog and **restart Home Assistant itself**. Reloading the integration or refreshing the browser does not replace its loaded Python config flow.
4. Open IM CAM setup again. It should ask only for **Account** and **Password**.

Existing IM CAM config entries can stay in place; their account and password still work with the bundled client profile. You do not need to delete your devices or entities to update.

If the old seven-field form remains after a restart, check that HACS lists **iliasgws/im-cam-home-assistant**. In Home Assistant's file editor, `custom_components/im_cam/manifest.json` should show version `0.3.0`, and `config_flow.py` should have only `CONF_ACCOUNT` and `CONF_PASSWORD` in its form schema. This distinguishes an old download from code still loaded in memory.

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

This implementation uses authenticated account/device authorization, service-provided STUN/TURN settings, and ephemeral signaling sessions. Session secrets and camera permits are kept in memory and are not entity attributes. Two-way talk, snapshot images, recording, and server-side transcoding are not implemented.

The controls and signaling are covered by mocked protocol/lifecycle tests and imports against Home Assistant 2026.10.4. End-to-end playback and controls have not yet been verified from a Home Assistant installation against a real camera. The existing Linux CLI's camera verification does not prove this new browser media path works on every camera or network.

The native asynchronous WebRTC camera API used here requires Home Assistant **2024.12.0 or newer**.
