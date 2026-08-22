"""Tests for fluks milestone 1 onboarding."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.data_entry_flow import _BaseFlowManagerView
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fluks.api import (
    FluksApiClient,
    FluksApiError,
    FluksCannotConnect,
    FluksInvalidCredentials,
    FluksInvalidVerificationCode,
    FluksValidationError,
)
from custom_components.fluks.config_flow import (
    CONF_CODE,
    CONF_SITE,
    CONF_SITE_NAME,
)
from custom_components.fluks.const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_TOKEN_EXPIRES_AT,
    CONF_INTEGRATION_ID,
    CONF_INTEGRATION_INTERNAL_ID,
    CONF_SITE_ID,
    DOMAIN,
)

SITE = {"id": "site-uuid", "name": "Home"}
EXTERNAL_ID = "11111111-1111-1111-1111-111111111111"
INTERNAL_ID = "22222222-2222-2222-2222-222222222222"


def make_api():
    """Return a flow API mock with successful authentication defaults."""
    api = MagicMock(spec=FluksApiClient)
    api.login = AsyncMock(return_value=("human-jwt", 3600))
    api.register_user = AsyncMock()
    api.verify_email = AsyncMock()
    api.resend_verification = AsyncMock()
    api.list_sites = AsyncMock(return_value=[SITE])
    api.list_integrations = AsyncMock(return_value=[])
    api.create_integration = AsyncMock(
        return_value={
            "id": INTERNAL_ID,
            "integrationId": EXTERNAL_ID,
            "siteId": SITE["id"],
            "type": "homeAssistant",
        }
    )
    api.create_site = AsyncMock(return_value=SITE)
    return api


async def start_flow(hass, api):
    """Start a flow with a deterministic external identity and API mock."""
    with (
        patch(
            "custom_components.fluks.config_flow.uuid4",
            return_value=UUID(EXTERNAL_ID),
        ),
        patch(
            "custom_components.fluks.config_flow.FluksApiClient",
            return_value=api,
        ),
    ):
        return await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )


async def choose_login(hass, flow_id):
    """Choose login from the initial native menu."""
    return await hass.config_entries.flow.async_configure(
        flow_id, {"next_step_id": "login"}
    )


async def choose_register(hass, flow_id):
    """Choose account creation from the initial native menu."""
    return await hass.config_entries.flow.async_configure(
        flow_id, {"next_step_id": "register"}
    )


async def choose_verify(hass, flow_id):
    """Choose code entry from the native verification menu."""
    return await hass.config_entries.flow.async_configure(
        flow_id, {"next_step_id": "verify"}
    )


async def reach_resend_form(hass, api):
    """Register and choose the native resend action."""
    result = await start_flow(hass, api)
    flow_id = result["flow_id"]
    result = await choose_register(hass, flow_id)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_EMAIL: "new@example.com", CONF_PASSWORD: "long-password"},
    )
    assert result["step_id"] == "verification"
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"next_step_id": "resend_verification"}
    )
    return result


def serialize_form(hass, result):
    """Serialize a form through Home Assistant's frontend response path."""
    return _BaseFlowManagerView(hass.config_entries.flow)._prepare_result_json(result)


async def test_flow_starts_with_authentication_choices(hass):
    """The initial flow offers only login and account creation."""
    result = await start_flow(hass, make_api())

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    assert result["menu_options"] == ["login", "register"]


async def test_auth_forms_are_clean_and_serializable(hass):
    """Authentication forms contain only their intended user inputs."""
    api = make_api()
    result = await start_flow(hass, api)
    flow_id = result["flow_id"]

    result = await choose_login(hass, flow_id)
    serialized = serialize_form(hass, result)
    assert [item["name"] for item in serialized["data_schema"]] == [
        CONF_EMAIL,
        CONF_PASSWORD,
    ]

    result = await start_flow(hass, api)
    flow_id = result["flow_id"]
    result = await choose_register(hass, flow_id)
    serialized = serialize_form(hass, result)
    assert [item["name"] for item in serialized["data_schema"]] == [
        CONF_EMAIL,
        CONF_PASSWORD,
        "first_name",
        "last_name",
    ]

    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_EMAIL: "new@example.com", CONF_PASSWORD: "long-password"},
    )
    assert result["step_id"] == "verification"
    result = await choose_verify(hass, flow_id)
    serialized = serialize_form(hass, result)
    assert [item["name"] for item in serialized["data_schema"]] == [CONF_CODE]


async def test_existing_user_login_and_site_selection(hass):
    """A user can authenticate, select a Site, and finish onboarding."""
    api = make_api()
    result = await start_flow(hass, api)
    result = await choose_login(hass, result["flow_id"])
    assert result["step_id"] == "login"
    serialize_form(hass, result)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "password"},
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "site_choice"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "site"}
    )
    serialize_form(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SITE: SITE["id"]}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    assert result["data"] == {
        CONF_SITE_ID: SITE["id"],
        CONF_INTEGRATION_ID: EXTERNAL_ID,
        CONF_INTEGRATION_INTERNAL_ID: INTERNAL_ID,
        CONF_ACCESS_TOKEN: "human-jwt",
        CONF_ACCESS_TOKEN_EXPIRES_AT: result["data"][CONF_ACCESS_TOKEN_EXPIRES_AT],
    }
    api.create_integration.assert_awaited_once_with(SITE["id"], EXTERNAL_ID)


async def test_invalid_credentials_are_recoverable(hass):
    """INVALID_CREDENTIALS leaves the login form retryable."""
    api = make_api()
    api.login.side_effect = FluksInvalidCredentials("INVALID_CREDENTIALS")
    result = await start_flow(hass, api)
    result = await choose_login(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "wrong"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "login"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_login_network_failure_is_recoverable(hass):
    """Connectivity failures leave the login form retryable."""
    api = make_api()
    api.login.side_effect = FluksCannotConnect()
    result = await start_flow(hass, api)
    result = await choose_login(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "password"},
    )

    assert result["step_id"] == "login"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_registration_verification_then_login(hass):
    """Generic registration success and verification return the user to login."""
    api = make_api()
    result = await start_flow(hass, api)
    result = await choose_register(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "new@example.com", CONF_PASSWORD: "long-password"},
    )
    assert result["step_id"] == "verification"
    assert result["menu_options"] == ["verify", "resend_verification"]
    result = await choose_verify(hass, result["flow_id"])
    assert result["step_id"] == "verify"
    serialized = serialize_form(hass, result)
    serialized_code = next(
        item for item in serialized["data_schema"] if item["name"] == CONF_CODE
    )
    assert serialized_code["name"] == CONF_CODE
    assert serialized_code["required"] is True
    assert "text" in serialized_code["selector"]
    api.register_user.assert_awaited_once_with(
        "new@example.com", "long-password", first_name=None, last_name=None
    )

    api.verify_email.side_effect = FluksInvalidVerificationCode(
        "INVALID_VERIFICATION_CODE"
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "042317"}
    )
    assert result["step_id"] == "verify"
    assert result["errors"] == {"base": "invalid_verification_code"}
    api.verify_email.assert_awaited_with("new@example.com", "042317")

    api.verify_email.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "042317"}
    )
    assert result["step_id"] == "login"
    assert api.login.await_count == 0


async def test_resend_recovery_preserves_flow_state_and_has_no_side_effects(hass):
    """The native resend action uses stored state and stays in the same flow."""
    api = make_api()
    result = await reach_resend_form(hass, api)
    flow_id = result["flow_id"]

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "resend_verification"
    assert result["description_placeholders"] == {"email": "new@example.com"}
    serialized = serialize_form(hass, result)
    assert serialized["data_schema"] == []
    api.resend_verification.assert_not_awaited()

    result = await hass.config_entries.flow.async_configure(flow_id, {})

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "verification_resent"
    assert result["flow_id"] == flow_id
    assert result["description_placeholders"] == {"email": "new@example.com"}
    api.resend_verification.assert_awaited_once_with("new@example.com")
    api.register_user.assert_awaited_once()
    api.verify_email.assert_not_awaited()
    api.login.assert_not_awaited()
    api.list_sites.assert_not_awaited()
    api.create_site.assert_not_awaited()
    api.list_integrations.assert_not_awaited()
    api.create_integration.assert_not_awaited()
    assert hass.config_entries.async_entries(DOMAIN) == []

    result = await choose_verify(hass, flow_id)
    assert result["step_id"] == "verify"
    assert result["description_placeholders"] == {"email": "new@example.com"}
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_CODE: "012345"}
    )
    assert result["step_id"] == "login"
    api.verify_email.assert_awaited_once_with("new@example.com", "012345")

    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_EMAIL: "new@example.com", CONF_PASSWORD: "long-password"},
    )
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"next_step_id": "site"}
    )
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_SITE: SITE["id"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_INTEGRATION_ID] == EXTERNAL_ID


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (FluksCannotConnect(), "resend_cannot_connect"),
        (FluksValidationError("VALIDATION_ERROR"), "resend_invalid_input"),
        (FluksApiError("INVALID_RESPONSE"), "resend_unknown"),
    ],
)
async def test_resend_failures_are_recoverable(hass, failure, error):
    """Resend failures preserve state and can be retried in place."""
    api = make_api()
    api.resend_verification.side_effect = [failure, None]
    result = await reach_resend_form(hass, api)
    flow_id = result["flow_id"]

    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "resend_verification"
    assert result["flow_id"] == flow_id
    assert result["errors"] == {"base": error}
    assert result["description_placeholders"] == {"email": "new@example.com"}
    serialize_form(hass, result)
    api.list_sites.assert_not_awaited()
    api.list_integrations.assert_not_awaited()
    api.create_integration.assert_not_awaited()

    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["step_id"] == "verification_resent"
    assert api.resend_verification.await_count == 2


async def test_local_verification_code_validation(hass):
    """Only six ASCII decimal digits are sent to the backend."""
    api = make_api()
    result = await start_flow(hass, api)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "register"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "new@example.com", CONF_PASSWORD: "long-password"},
    )
    result = await choose_verify(hass, result["flow_id"])

    for invalid_code in ("12345", "1234567", "12a456", "٠١٢٣٤٥"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CODE: invalid_code}
        )
        assert result["step_id"] == "verify"
        assert result["errors"] == {
            CONF_CODE: "invalid_verification_code_format"
        }

    api.verify_email.assert_not_awaited()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "012345"}
    )
    assert result["step_id"] == "login"
    api.verify_email.assert_awaited_once_with("new@example.com", "012345")


async def test_registration_validation_error(hass):
    """Registration validation failures remain recoverable."""
    api = make_api()
    api.register_user.side_effect = FluksValidationError("VALIDATION_ERROR")
    result = await start_flow(hass, api)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "register"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "new@example.com", CONF_PASSWORD: "short"},
    )

    assert result["step_id"] == "register"
    assert result["errors"] == {"base": "invalid_input"}


async def test_no_sites_uses_home_assistant_location(hass):
    """Site creation asks only for name and uses Home Assistant metadata."""
    hass.config.latitude = 55.6761
    hass.config.longitude = 12.5683
    hass.config.time_zone = "Europe/Copenhagen"
    api = make_api()
    api.list_sites.return_value = []
    result = await start_flow(hass, api)
    result = await choose_login(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "password"},
    )
    assert result["step_id"] == "create_site"
    serialize_form(hass, result)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SITE_NAME: "Home"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    api.create_site.assert_awaited_once_with(
        "Home", 55.6761, 12.5683, "Europe/Copenhagen", "ordinary_residential"
    )


async def test_existing_integration_is_reused(hass):
    """A matching external identity reuses the documented list result."""
    api = make_api()
    api.list_integrations.return_value = [
        {
            "id": INTERNAL_ID,
            "integrationId": EXTERNAL_ID,
            "siteId": SITE["id"],
            "type": "homeAssistant",
        }
    ]
    result = await start_flow(hass, api)
    result = await choose_login(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "password"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "site"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SITE: SITE["id"]}
    )

    assert result["data"][CONF_INTEGRATION_ID] == EXTERNAL_ID
    assert result["data"][CONF_INTEGRATION_INTERNAL_ID] == INTERNAL_ID
    api.create_integration.assert_not_awaited()


async def test_registration_retry_keeps_external_identity(hass):
    """A failed registration retry reuses the flow's stable external identity."""
    api = make_api()
    integration = {
        "id": INTERNAL_ID,
        "integrationId": EXTERNAL_ID,
        "siteId": SITE["id"],
        "type": "homeAssistant",
    }
    api.list_integrations.side_effect = [FluksCannotConnect(), [integration]]
    result = await start_flow(hass, api)
    result = await choose_login(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "password"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "site"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SITE: SITE["id"]}
    )
    assert result["step_id"] == "register_integration"
    assert result["errors"] == {"base": "cannot_connect"}
    serialize_form(hass, result)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_INTEGRATION_ID] == EXTERNAL_ID
    api.create_integration.assert_not_awaited()


async def test_duplicate_site_aborts_before_backend_registration(hass):
    """Home Assistant's unique ID prevents duplicate Site configuration."""
    MockConfigEntry(domain=DOMAIN, unique_id=SITE["id"], data={}).add_to_hass(hass)
    api = make_api()
    result = await start_flow(hass, api)
    result = await choose_login(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "password"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "site"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SITE: SITE["id"]}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    api.list_integrations.assert_not_awaited()
    api.create_integration.assert_not_awaited()
