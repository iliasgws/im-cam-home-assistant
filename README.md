# IM CAM for Home Assistant

[![Open your Home Assistant instance and open IM CAM in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=iliasgws&repository=im-cam-home-assistant&category=integration)

This custom integration connects directly to the IM CAM control service and creates one connection sensor per linked camera. It currently reports `online`, `offline`, or `unknown`; video streaming and camera controls are not exposed in Home Assistant yet. It does not require the IM-CAM CLI, Java, or a local client profile file.

## Requirements

- Home Assistant Core with custom integrations enabled.
- The account must have linked cameras.

## Install

Install with the **Open in HACS** badge above, then **restart Home Assistant** before adding **IM CAM** under Settings → Devices & services. Enter your IM CAM account and password; client registration is bundled with the integration. The integration polls every two minutes.

The HACS repository is **iliasgws/im-cam-home-assistant** on GitHub. The GitLab project holds the Android app and a copy of this integration.

Your account password is stored in the Home Assistant config entry and hashed in memory when constructing the login request. The client registration profile is shared by users of this integration. Keep a backup of the Home Assistant configuration private.

## Update an existing installation

1. Open **IM CAM** in HACS and choose **Update information** from its menu.
2. Install version **0.2.0** or newer. If HACS does not offer an update for an earlier commit-based installation, choose **Redownload**, select **0.2.0**, and download it.
3. Close any open IM CAM setup dialog and **restart Home Assistant itself**. Reloading the integration or refreshing the browser does not replace its loaded Python config flow.
4. Open IM CAM setup again. It should ask only for **Account** and **Password**.

Existing IM CAM config entries can stay in place; their account and password still work with the bundled client profile. You do not need to delete your devices or entities to update.

If the old seven-field form remains after a restart, check that HACS lists **iliasgws/im-cam-home-assistant**. In Home Assistant's file editor, `custom_components/im_cam/manifest.json` should show version `0.2.0`, and `config_flow.py` should have only `CONF_ACCOUNT` and `CONF_PASSWORD` in its form schema. This distinguishes an old download from code still loaded in memory.

## Entities

Each camera has a **Connection** sensor with the camera serial and online boolean as attributes. The entity device uses the camera alias reported by the service.
