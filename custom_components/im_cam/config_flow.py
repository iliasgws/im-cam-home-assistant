"""Config flow for IM CAM."""

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
import voluptuous as vol

from .const import CONF_ACCOUNT, CONF_PASSWORD, DOMAIN


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
