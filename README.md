# IM CAM for Home Assistant

[![Open your Home Assistant instance and open IM CAM in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=iliasgws&repository=im-cam-home-assistant&category=integration)

This custom integration connects directly to the IM CAM control service and creates one connection sensor per linked camera. It currently reports `online`, `offline`, or `unknown`; video streaming and camera controls are not exposed in Home Assistant yet. It does not require the IM-CAM CLI, Java, or a local client profile file.

## Requirements

- Home Assistant Core with custom integrations enabled.
- The account must have linked cameras.

## Install

Install with the **Open in HACS** badge above, then add **IM CAM** under Settings → Devices & services. Enter your IM CAM account and password; client registration is bundled with the integration. The integration polls every two minutes.

Once this repository is published on GitHub, it can be installed through HACS using the one-click **Open in HACS** link. HACS does not support installing custom repositories hosted on GitLab.

Your account password is stored in the Home Assistant config entry and hashed in memory when constructing the login request. The client registration profile is shared by users of this integration. Keep a backup of the Home Assistant configuration private.

## Entities

Each camera has a **Connection** sensor with the camera serial and online boolean as attributes. The entity device uses the camera alias reported by the service.
