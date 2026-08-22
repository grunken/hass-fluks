"""Tests for the milestone 1 config entry lifecycle."""

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fluks.const import DOMAIN


async def test_setup_unload_and_reload_have_no_backend_side_effects(hass):
    """Load, unload, and reload never register backend resources."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="site-uuid",
        data={
            "site_id": "site-uuid",
            "integration_id": "external-id",
            "integration_internal_id": "internal-id",
            "access_token": "jwt",
            "access_token_expires_at": 1234567890,
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.fluks.api.FluksApiClient.create_integration"
    ) as create_integration:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert await hass.config_entries.async_setup(entry.entry_id)
        create_integration.assert_not_called()

    assert entry.data["integration_id"] == "external-id"
