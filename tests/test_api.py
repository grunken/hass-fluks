"""Tests for the milestone 1 fluks API contract."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.fluks.api import (
    FluksApiClient,
    FluksCannotConnect,
    FluksConflict,
    FluksInvalidCredentials,
    FluksInvalidVerificationCode,
    FluksNotFound,
    FluksUnauthorized,
    FluksValidationError,
)
from custom_components.fluks.const import API_BASE_URL

INTEGRATION_KEY = "fluks_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ"
ROTATED_INTEGRATION_KEY = "fluks_0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"


class FakeResponse:
    """Minimal aiohttp response context manager."""

    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        return self._body


class FakeSession:
    """Record requests and return queued responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_public_authentication_contract():
    """Public requests use exact paths and documented payload field names."""
    session = FakeSession(
        FakeResponse(202, {"message": "requested"}),
        FakeResponse(200, {"message": "verified"}),
        FakeResponse(200, {"data": {"accessToken": "jwt", "expiresIn": 3600}}),
    )
    client = FluksApiClient(session)

    await client.register_user(
        "user@example.com", "very-long-password", first_name="A", last_name="B"
    )
    await client.verify_email("user@example.com", "042317")
    assert await client.login("user@example.com", "very-long-password") == (
        "jwt",
        3600,
    )

    assert [request[0:2] for request in session.requests] == [
        ("POST", f"{API_BASE_URL}/users"),
        ("POST", f"{API_BASE_URL}/users/email-verification/verify"),
        ("POST", f"{API_BASE_URL}/auth/login"),
    ]
    assert session.requests[0][2]["json"] == {
        "email": "user@example.com",
        "password": "very-long-password",
        "firstName": "A",
        "lastName": "B",
    }
    assert session.requests[1][2]["json"]["code"] == "042317"
    assert all("Authorization" not in item[2]["headers"] for item in session.requests)


@pytest.mark.asyncio
async def test_authenticated_site_and_integration_contract():
    """Authenticated requests use the human bearer token and exact schemas."""
    site = {"id": "site-id", "name": "Home"}
    integration = {
        "id": "internal-id",
        "integrationId": "external-id",
        "siteId": "site-id",
        "type": "homeAssistant",
    }
    session = FakeSession(
        FakeResponse(200, {"data": [site]}),
        FakeResponse(201, {"data": site}),
        FakeResponse(200, {"data": [integration]}),
        FakeResponse(
            201, {"data": {**integration, "integrationKey": INTEGRATION_KEY}}
        ),
        FakeResponse(
            201, {"data": {"integrationKey": ROTATED_INTEGRATION_KEY}}
        ),
    )
    client = FluksApiClient(session, human_access_token="human-jwt")

    assert await client.list_sites() == [site]
    assert await client.create_site(
        "Home", 55.1, 12.2, "Europe/Copenhagen", "ordinary_residential"
    ) == site
    assert await client.list_integrations("site-id") == [integration]
    assert await client.create_integration("site-id", "external-id") == {
        **integration,
        "integrationKey": INTEGRATION_KEY,
    }
    assert await client.issue_integration_key("site-id", "internal-id") == (
        ROTATED_INTEGRATION_KEY
    )

    assert all(
        item[2]["headers"] == {"Authorization": "Bearer human-jwt"}
        for item in session.requests
    )
    assert session.requests[1][2]["json"] == {
        "name": "Home",
        "location": {"latitude": 55.1, "longitude": 12.2},
        "timezone": "Europe/Copenhagen",
        "energyProfile": "ordinary_residential",
    }
    assert session.requests[3][2]["json"] == {
        "integrationId": "external-id",
        "type": "homeAssistant",
    }
    assert session.requests[4][0:2] == (
        "POST",
        f"{API_BASE_URL}/sites/site-id/integrations/internal-id/integration-key",
    )


@pytest.mark.asyncio
async def test_catalog_device_and_mapping_contracts():
    """Milestone 2 uses exact documented paths, identities, and payloads."""
    catalog = [
        {
            "type": "battery",
            "concepts": [
                {
                    "concept": "battery.soc",
                    "datatype": "number",
                    "cadence": "realtime",
                    "usages": ["fact"],
                    "source": "mapping",
                }
            ],
        }
    ]
    device = {
        "id": "22222222-2222-2222-2222-222222222222",
        "deviceId": "external-device-id",
        "type": "battery",
    }
    mapping = {
        "integrationId": "11111111-1111-1111-1111-111111111111",
        "deviceId": device["id"],
        "concept": "battery.soc",
        "direction": "input",
        "configuration": {"version": 1, "entityId": "sensor.battery_soc"},
    }
    session = FakeSession(
        FakeResponse(200, {"data": catalog}),
        FakeResponse(200, {"data": [device]}),
        FakeResponse(201, {"data": device}),
        FakeResponse(200, {"data": []}),
        FakeResponse(201, {"data": {"id": "mapping-id", **mapping}}),
    )
    client = FluksApiClient(
        session,
        human_access_token="expired-human-jwt",
        integration_key="integration-key",
    )

    assert await client.get_device_type_catalog() == catalog
    assert await client.list_devices("site-id") == [device]
    assert await client.create_device(
        "site-id",
        "external-device-id",
        "battery",
        {"displayName": "GoodWe", "vendor": "GoodWe"},
    ) == device
    assert await client.list_mappings("site-id") == []
    await client.create_mapping("site-id", mapping)

    assert [item[0:2] for item in session.requests] == [
        ("GET", f"{API_BASE_URL}/canonical/device-types"),
        ("GET", f"{API_BASE_URL}/sites/site-id/devices"),
        ("POST", f"{API_BASE_URL}/sites/site-id/devices"),
        ("GET", f"{API_BASE_URL}/sites/site-id/mappings"),
        ("POST", f"{API_BASE_URL}/sites/site-id/mappings"),
    ]
    assert session.requests[0][2]["headers"] == {}
    assert all(
        item[2]["headers"] == {"Authorization": "Bearer integration-key"}
        for item in session.requests[1:]
    )
    assert session.requests[2][2]["json"] == {
        "deviceId": "external-device-id",
        "type": "battery",
        "properties": {"displayName": "GoodWe", "vendor": "GoodWe"},
    }
    assert session.requests[4][2]["json"] == mapping


@pytest.mark.asyncio
async def test_device_management_uses_filtered_incremental_machine_contracts():
    """Milestone 3 uses integration auth and only documented incremental APIs."""
    device = {
        "id": "device-internal",
        "deviceId": "device-external",
        "type": "solar",
        "properties": {"azimuthDegrees": 180},
    }
    mapping = {
        "id": "mapping-id",
        "concept": "solar.power",
        "direction": "input",
        "configuration": {"version": 1, "entityId": "sensor.solar_power"},
    }
    session = FakeSession(
        FakeResponse(200, {"data": device}),
        FakeResponse(200, {"data": {**device, "properties": {"azimuthDegrees": 190}}}),
        FakeResponse(200, {"data": [mapping]}),
        FakeResponse(200, {"data": {**mapping, "configuration": {"version": 1, "entityId": "sensor.new"}}}),
        FakeResponse(204, None),
    )
    client = FluksApiClient(
        session, human_access_token="expired", integration_key="machine-key"
    )

    await client.get_device("site-id", "device-internal")
    await client.update_device_properties(
        "site-id", "device-internal", {"azimuthDegrees": 190}
    )
    await client.list_mappings("site-id", device_id="device-internal")
    await client.update_mapping(
        "site-id", "mapping-id", {"version": 1, "entityId": "sensor.new"}
    )
    await client.delete_mapping("site-id", "mapping-id")

    assert [request[0] for request in session.requests] == [
        "GET", "PATCH", "GET", "PATCH", "DELETE"
    ]
    assert session.requests[1][2]["json"] == {
        "properties": {"azimuthDegrees": 190}
    }
    assert session.requests[2][2]["params"] == {"deviceId": "device-internal"}
    assert session.requests[3][2]["json"] == {
        "configuration": {"version": 1, "entityId": "sensor.new"}
    }
    assert all(
        request[2]["headers"] == {"Authorization": "Bearer machine-key"}
        for request in session.requests
    )


@pytest.mark.asyncio
async def test_battery_property_patch_preserves_exact_names_values_and_response():
    """Battery physical configuration crosses the real HTTP client boundary losslessly."""
    properties = {
        "capacityKwh": 18.5,
        "battery.socMinimum": 12.5,
        "battery.socMaximum": 94.5,
    }
    updated = {
        "id": "battery-internal",
        "deviceId": "battery-external",
        "type": "battery",
        "properties": properties,
    }
    session = FakeSession(FakeResponse(200, {"data": updated}))
    client = FluksApiClient(session, integration_key="machine-key")

    assert await client.update_device_properties(
        "site-id", "battery-internal", properties
    ) == updated
    method, url, request = session.requests[0]
    assert (method, url) == (
        "PATCH",
        f"{API_BASE_URL}/sites/site-id/devices/battery-internal",
    )
    assert request["json"] == {"properties": properties}
    assert request["headers"] == {"Authorization": "Bearer machine-key"}


@pytest.mark.asyncio
async def test_device_lifecycle_delete_uses_only_human_auth():
    """The administrative DELETE never uses the Integration credential."""
    session = FakeSession(FakeResponse(204, None))
    client = FluksApiClient(
        session,
        human_access_token="temporary-human-jwt",
        integration_key="integration-key",
    )

    await client.delete_device("site-id", "device-internal")

    assert len(session.requests) == 1
    method, url, kwargs = session.requests[0]
    assert method == "DELETE"
    assert url == f"{API_BASE_URL}/sites/site-id/devices/device-internal"
    assert kwargs["headers"] == {
        "Authorization": "Bearer temporary-human-jwt"
    }
    assert kwargs["json"] is None
    assert kwargs["params"] is None


@pytest.mark.asyncio
async def test_site_lifecycle_delete_uses_only_human_auth():
    """Site deletion uses the documented administrative endpoint and JWT."""
    session = FakeSession(FakeResponse(204, None))
    client = FluksApiClient(
        session,
        human_access_token="temporary-human-jwt",
        integration_key="integration-key",
    )

    await client.delete_site("site-id")

    assert len(session.requests) == 1
    method, url, kwargs = session.requests[0]
    assert method == "DELETE"
    assert url == f"{API_BASE_URL}/sites/site-id"
    assert kwargs["headers"] == {
        "Authorization": "Bearer temporary-human-jwt"
    }
    assert kwargs["json"] is None
    assert kwargs["params"] is None


@pytest.mark.asyncio
async def test_device_lifecycle_delete_404_is_not_reported_as_success():
    """A foreign-or-missing Device response crosses the API boundary."""
    session = FakeSession(
        FakeResponse(404, {"error": {"code": "NOT_FOUND"}})
    )
    client = FluksApiClient(
        session,
        human_access_token="valid-foreign-user-jwt",
        integration_key="integration-key",
    )

    with pytest.raises(FluksNotFound):
        await client.delete_device("site-id", "device-internal")

    assert len(session.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "exception"),
    [
        (401, "INVALID_CREDENTIALS", FluksInvalidCredentials),
        (400, "INVALID_VERIFICATION_CODE", FluksInvalidVerificationCode),
        (400, "VALIDATION_ERROR", FluksValidationError),
        (401, "UNAUTHORIZED", FluksUnauthorized),
        (409, "DEVICE_ID_CONFLICT", FluksConflict),
        (404, "NOT_FOUND", FluksNotFound),
    ],
)
async def test_documented_errors(status, code, exception):
    """Documented backend codes cross the API boundary as expected failures."""
    session = FakeSession(FakeResponse(status, {"error": {"code": code}}))
    client = FluksApiClient(session)

    with pytest.raises(exception):
        await client.verify_email("user@example.com", "123456")


@pytest.mark.asyncio
async def test_timeout_is_connectivity_failure():
    """Timeouts do not leak through the API boundary."""
    client = FluksApiClient(FakeSession(asyncio.TimeoutError()))

    with pytest.raises(FluksCannotConnect):
        await client.login("user@example.com", "password")


@pytest.mark.asyncio
async def test_rejected_integration_key_never_falls_back_or_leaks_secret():
    """A machine 401 is returned once without retrying with the human JWT."""
    integration_key = "fluks_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ"
    session = FakeSession(
        FakeResponse(401, {"error": {"code": "UNAUTHORIZED"}})
    )
    client = FluksApiClient(
        session,
        human_access_token="still-valid-human-jwt",
        integration_key=integration_key,
    )

    with pytest.raises(FluksUnauthorized) as raised:
        await client.list_devices("site-id")

    assert len(session.requests) == 1
    assert session.requests[0][2]["headers"] == {
        "Authorization": f"Bearer {integration_key}"
    }
    assert integration_key not in str(raised.value)


@pytest.mark.asyncio
async def test_missing_auth_context_credential_fails_before_http():
    """Machine operations cannot silently use a different credential context."""
    session = FakeSession()
    client = FluksApiClient(session, human_access_token="human-jwt")

    with pytest.raises(FluksUnauthorized):
        await client.list_mappings("site-id")

    assert session.requests == []
