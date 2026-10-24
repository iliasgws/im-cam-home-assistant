# IM CAM for Home Assistant

[![Open your Home Assistant instance and open IM CAM in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pilmomo&repository=im-cam-home-assistant&category=integration)

This custom integration connects directly to the IM CAM control service and creates one connection sensor per linked camera. It currently reports `online`, `offline`, or `unknown`; video streaming and camera controls are not exposed in Home Assistant yet. It does not require the IM-CAM CLI, Java, or a local client profile file.

## Requirements

- Home Assistant Core with custom integrations enabled.
- The authorized service registration values (`vid`, `vkey`, `oemVid`, `bundleId`, `appVersion`) for your IM CAM client profile.
- The account must have linked cameras.

## Install

Copy this `im_cam` directory to `<config>/custom_components/im_cam/`, restart Home Assistant, then add **IM CAM** under Settings → Devices & services. Enter the account, password, and the five service registration values. The integration polls every two minutes.

Once this repository is published on GitHub, it can be installed through HACS using the one-click **Open in HACS** link. HACS does not support installing custom repositories hosted on GitLab.

The account password and service registration values are stored in the Home Assistant config entry. The password is only hashed in memory when constructing the login request. Keep a backup of the Home Assistant configuration private.

## Entities

Each camera has a **Connection** sensor with the camera serial and online boolean as attributes. The entity device uses the camera alias reported by the service.
