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
    FluksUnauthorized,
    FluksValidationError,
)
from custom_components.fluks.const import API_BASE_URL


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
        FakeResponse(201, {"data": integration}),
    )
    client = FluksApiClient(session, "human-jwt")

    assert await client.list_sites() == [site]
    assert await client.create_site(
        "Home", 55.1, 12.2, "Europe/Copenhagen", "ordinary_residential"
    ) == site
    assert await client.list_integrations("site-id") == [integration]
    assert await client.create_integration("site-id", "external-id") == integration

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
    client = FluksApiClient(session, "human-jwt")

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
        item[2]["headers"] == {"Authorization": "Bearer human-jwt"}
        for item in session.requests[1:]
    )
    assert session.requests[2][2]["json"] == {
        "deviceId": "external-device-id",
        "type": "battery",
        "properties": {"displayName": "GoodWe", "vendor": "GoodWe"},
    }
    assert session.requests[4][2]["json"] == mapping


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "exception"),
    [
        (401, "INVALID_CREDENTIALS", FluksInvalidCredentials),
        (400, "INVALID_VERIFICATION_CODE", FluksInvalidVerificationCode),
        (400, "VALIDATION_ERROR", FluksValidationError),
        (401, "UNAUTHORIZED", FluksUnauthorized),
        (409, "DEVICE_ID_CONFLICT", FluksConflict),
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
