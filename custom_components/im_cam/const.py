from datetime import timedelta

DOMAIN = "im_cam"
CONF_ACCOUNT = "account"
CONF_PASSWORD = "password"
CONF_ENABLE_TRANSCODING = "enable_server_transcoding"
CONF_TRANSCODER_URL = "transcoder_url"
CONF_TRANSCODER_API_KEY = "transcoder_api_key"
DEFAULT_TRANSCODER_URL = "http://feacc790-imcam-transcoder:8787"
DEFAULT_SCAN_INTERVAL = timedelta(minutes=2)
