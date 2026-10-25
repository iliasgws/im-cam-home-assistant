"""Config flow for IM CAM."""

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.core import callback
import voluptuous as vol

from .const import (
    CONF_ACCOUNT,
    CONF_ENABLE_TRANSCODING,
    CONF_PASSWORD,
    CONF_TRANSCODER_API_KEY,
    CONF_TRANSCODER_URL,
    DEFAULT_TRANSCODER_URL,
    DOMAIN,
)


class ImCamConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_ACCOUNT].strip().lower())
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input[CONF_ACCOUNT], data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_ACCOUNT): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ImCamOptionsFlow()


class ImCamOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None) -> FlowResult:
        if user_input is not None:
            errors = {}
            if user_input.get(CONF_ENABLE_TRANSCODING):
                url = user_input.get(CONF_TRANSCODER_URL, "")
                if not url.startswith(("http://", "https://")):
                    errors["base"] = "invalid_transcoder_url"
                if len(user_input.get(CONF_TRANSCODER_API_KEY, "")) < 32:
                    errors["base"] = "api_key_too_short"
            if not errors:
                return self.async_create_entry(title="", data=user_input)
        else:
            errors = {}

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ENABLE_TRANSCODING,
                    default=options.get(CONF_ENABLE_TRANSCODING, False),
                ): bool,
                vol.Optional(
                    CONF_TRANSCODER_URL,
                    default=options.get(CONF_TRANSCODER_URL, DEFAULT_TRANSCODER_URL),
                ): str,
                vol.Optional(
                    CONF_TRANSCODER_API_KEY,
                    default=options.get(CONF_TRANSCODER_API_KEY, ""),
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
