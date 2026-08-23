"""Config flow for fluks milestone 1 onboarding."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    FluksApiClient,
    FluksApiError,
    FluksCannotConnect,
    FluksInvalidCredentials,
    FluksInvalidVerificationCode,
    FluksUnauthorized,
    FluksValidationError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_TOKEN_EXPIRES_AT,
    CONF_INTEGRATION_ID,
    CONF_INTEGRATION_INTERNAL_ID,
    CONF_INTEGRATION_KEY,
    CONF_SITE_ID,
    DOMAIN,
    ENERGY_PROFILE,
)

CONF_CODE = "code"
CONF_FIRST_NAME = "first_name"
CONF_LAST_NAME = "last_name"
CONF_SITE = "site"
CONF_SITE_NAME = "site_name"


class FluksConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the native Home Assistant onboarding flow for fluks."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the native Milestone 2 Add Device flow."""
        from .options_flow import FluksOptionsFlow

        return FluksOptionsFlow()

    def __init__(self) -> None:
        self._api: FluksApiClient | None = None
        self._registration_email: str | None = None
        self._sites: dict[str, dict[str, Any]] = {}
        self._selected_site: dict[str, Any] | None = None
        self._integration_id = str(uuid4())
        self._reauth_entry: ConfigEntry | None = None

    @property
    def api(self) -> FluksApiClient:
        """Return the flow-scoped API client."""
        if self._api is None:
            self._api = FluksApiClient(async_get_clientsession(self.hass))
        return self._api

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Offer login or account creation."""
        # Create one flow-scoped client so its token and identity survive retries.
        _ = self.api
        return self.async_show_menu(step_id="user", menu_options=["login", "register"])

    async def async_step_login(self, user_input: dict[str, Any] | None = None):
        """Authenticate an existing or newly verified user."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token, _expires_in = await self.api.login(
                    user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
                self.api.set_human_access_token(token)
                return await self._load_sites()
            except FluksInvalidCredentials:
                errors["base"] = "invalid_auth"
            except FluksValidationError:
                errors["base"] = "invalid_input"
            except FluksCannotConnect:
                errors["base"] = "cannot_connect"
            except FluksUnauthorized:
                errors["base"] = "unauthorized"
            except FluksApiError:
                errors["base"] = "unknown"

        email_key = vol.Required(CONF_EMAIL)
        if self._registration_email is not None:
            email_key = vol.Required(CONF_EMAIL, default=self._registration_email)
        return self.async_show_form(
            step_id="login",
            data_schema=vol.Schema(
                {
                    email_key: selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.EMAIL
                        )
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_register(self, user_input: dict[str, Any] | None = None):
        """Create a pending fluks user."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self.api.register_user(
                    user_input[CONF_EMAIL],
                    user_input[CONF_PASSWORD],
                    first_name=user_input.get(CONF_FIRST_NAME),
                    last_name=user_input.get(CONF_LAST_NAME),
                )
                self._registration_email = user_input[CONF_EMAIL]
                return await self.async_step_verify()
            except FluksValidationError:
                errors["base"] = "invalid_input"
            except FluksCannotConnect:
                errors["base"] = "cannot_connect"
            except FluksApiError:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="register",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.EMAIL
                        )
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                    vol.Optional(CONF_FIRST_NAME): str,
                    vol.Optional(CONF_LAST_NAME): str,
                }
            ),
            errors=errors,
        )

    async def async_step_verify(self, user_input: dict[str, Any] | None = None):
        """Verify the email address used during registration."""
        if self._registration_email is None:
            return self.async_abort(reason="missing_registration")

        errors: dict[str, str] = {}
        if user_input is not None:
            code = user_input[CONF_CODE]
            if len(code) != 6 or not code.isascii() or not code.isdecimal():
                errors[CONF_CODE] = "invalid_verification_code_format"
            else:
                try:
                    await self.api.verify_email(self._registration_email, code)
                    return await self.async_step_login()
                except FluksInvalidVerificationCode:
                    errors["base"] = "invalid_verification_code"
                except FluksValidationError:
                    errors["base"] = "invalid_input"
                except FluksCannotConnect:
                    errors["base"] = "cannot_connect"
                except FluksApiError:
                    errors["base"] = "unknown"

        return self.async_show_form(
            step_id="verify",
            data_schema=vol.Schema(
                {vol.Required(CONF_CODE): selector.TextSelector()}
            ),
            errors=errors,
            description_placeholders={"email": self._registration_email},
        )

    async def _load_sites(self):
        """Fetch Sites after successful authentication."""
        sites = await self.api.list_sites()
        self._sites = {str(site["id"]): site for site in sites}
        if not self._sites:
            return await self.async_step_create_site()
        return await self.async_step_site_choice()

    async def async_step_site_choice(
        self, user_input: dict[str, Any] | None = None
    ):
        """Offer existing Site selection or Site creation."""
        return self.async_show_menu(
            step_id="site_choice", menu_options=["site", "create_site"]
        )

    async def async_step_site(self, user_input: dict[str, Any] | None = None):
        """Select an existing Site."""
        if user_input is not None:
            return await self._finish_site(self._sites[user_input[CONF_SITE]])

        choices = {site_id: str(site["name"]) for site_id, site in self._sites.items()}
        return self.async_show_form(
            step_id="site",
            data_schema=vol.Schema({vol.Required(CONF_SITE): vol.In(choices)}),
        )

    async def async_step_create_site(
        self, user_input: dict[str, Any] | None = None
    ):
        """Create a Site using Home Assistant's configured location."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                site = await self.api.create_site(
                    user_input[CONF_SITE_NAME],
                    self.hass.config.latitude,
                    self.hass.config.longitude,
                    self.hass.config.time_zone,
                    ENERGY_PROFILE,
                )
                return await self._finish_site(site)
            except FluksValidationError:
                errors["base"] = "invalid_input"
            except FluksUnauthorized:
                errors["base"] = "unauthorized"
            except FluksCannotConnect:
                errors["base"] = "cannot_connect"
            except FluksApiError:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="create_site",
            data_schema=vol.Schema({vol.Required(CONF_SITE_NAME): str}),
            errors=errors,
        )

    async def _finish_site(self, site: dict[str, Any]):
        """Keep the selected Site and begin recoverable registration."""
        self._selected_site = site
        site_id = str(site["id"])
        await self.async_set_unique_id(site_id)
        self._abort_if_unique_id_configured()
        return await self.async_step_register_integration({})

    async def async_step_register_integration(
        self, user_input: dict[str, Any] | None = None
    ):
        """Reuse or register the external Integration, allowing safe retries."""
        if self._selected_site is None:
            return self.async_abort(reason="unknown")

        site = self._selected_site
        site_id = str(site["id"])
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                integrations = await self.api.list_integrations(site_id)
                integration = next(
                    (
                        item
                        for item in integrations
                        if item.get("integrationId") == self._integration_id
                    ),
                    None,
                )
                if integration is None:
                    integration = await self.api.create_integration(
                        site_id, self._integration_id
                    )
                    integration_key = integration.get("integrationKey")
                    if not isinstance(integration_key, str):
                        raise FluksApiError("INVALID_RESPONSE")
                else:
                    integration_key = await self.api.issue_integration_key(
                        site_id, str(integration["id"])
                    )

                return self.async_create_entry(
                    title=str(site["name"]),
                    data={
                        CONF_SITE_ID: site_id,
                        CONF_INTEGRATION_ID: self._integration_id,
                        CONF_INTEGRATION_INTERNAL_ID: str(integration["id"]),
                        CONF_INTEGRATION_KEY: integration_key,
                    },
                )
            except FluksUnauthorized:
                errors["base"] = "unauthorized"
            except FluksCannotConnect:
                errors["base"] = "cannot_connect"
            except FluksApiError:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="register_integration", data_schema=vol.Schema({}), errors=errors
        )

    async def async_step_reauth(self, _entry_data: dict[str, Any]):
        """Start native credential recovery for an existing ConfigEntry."""
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        """Use a fresh human login to provision the existing Integration key."""
        if self._reauth_entry is None:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token, _expires_in = await self.api.login(
                    user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
                self.api.set_human_access_token(token)
                integration_key = await self.api.issue_integration_key(
                    self._reauth_entry.data[CONF_SITE_ID],
                    self._reauth_entry.data[CONF_INTEGRATION_INTERNAL_ID],
                )
                updated_data = dict(self._reauth_entry.data)
                updated_data.pop(CONF_ACCESS_TOKEN, None)
                updated_data.pop(CONF_ACCESS_TOKEN_EXPIRES_AT, None)
                updated_data[CONF_INTEGRATION_KEY] = integration_key
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data=updated_data,
                    reason="reauth_successful",
                )
            except FluksInvalidCredentials:
                errors["base"] = "invalid_auth"
            except FluksValidationError:
                errors["base"] = "invalid_input"
            except FluksCannotConnect:
                errors["base"] = "cannot_connect"
            except FluksUnauthorized:
                errors["base"] = "unauthorized"
            except FluksApiError:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.EMAIL
                        )
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )
