"""Small asynchronous client for fluks onboarding and device configuration."""

from __future__ import annotations

import asyncio
from enum import StrEnum
import re
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import API_BASE_URL, API_TIMEOUT_SECONDS, INTEGRATION_TYPE

INTEGRATION_KEY_PATTERN = re.compile(r"^fluks_[A-Za-z0-9_-]{43}$")
_UNSET = object()


class FluksApiError(Exception):
    """Base class for expected fluks API failures."""

    def __init__(self, code: str | None = None) -> None:
        super().__init__(code)
        self.code = code


class FluksCannotConnect(FluksApiError):
    """The fluks API could not be reached."""


class FluksValidationError(FluksApiError):
    """The fluks API rejected submitted data."""


class FluksInvalidCredentials(FluksApiError):
    """The supplied credentials are invalid."""


class FluksInvalidVerificationCode(FluksApiError):
    """The supplied verification code is unavailable or invalid."""


class FluksUnauthorized(FluksApiError):
    """The human access token is missing, invalid, or expired."""


class FluksConflict(FluksApiError):
    """A stable backend identity or mapping already exists."""


class FluksNotFound(FluksApiError):
    """The requested backend resource is already unavailable."""


class AuthContext(StrEnum):
    """Explicit credential context for one backend operation."""

    PUBLIC = "public"
    HUMAN = "human"
    INTEGRATION = "integration"


class FluksApiClient:
    """Access only the fluks operations needed by implemented flows."""

    def __init__(
        self,
        session: ClientSession,
        *,
        human_access_token: str | None = None,
        integration_key: str | None = None,
        base_url: str = API_BASE_URL,
    ) -> None:
        self._session = session
        self._human_access_token = human_access_token
        self._integration_key = integration_key
        self._base_url = base_url.rstrip("/")

    def set_human_access_token(self, access_token: str | None) -> None:
        """Set or discard the temporary human JWT."""
        self._human_access_token = access_token

    async def get_device_type_catalog(self) -> list[dict[str, Any]]:
        """Return the public backend-owned canonical Device catalog."""
        response = await self._request(
            "GET",
            "/canonical/device-types",
            expected_status=200,
            auth=AuthContext.PUBLIC,
        )
        data = self._response_data(response)
        if not isinstance(data, list):
            raise FluksApiError("INVALID_RESPONSE")
        return data

    async def register_user(
        self,
        email: str,
        password: str,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> None:
        """Register a pending user and request its verification email."""
        payload: dict[str, str] = {"email": email, "password": password}
        if first_name:
            payload["firstName"] = first_name
        if last_name:
            payload["lastName"] = last_name
        await self._request(
            "POST",
            "/users",
            json=payload,
            expected_status=202,
            auth=AuthContext.PUBLIC,
        )

    async def verify_email(self, email: str, code: str) -> None:
        """Verify a pending user's email address."""
        await self._request(
            "POST",
            "/users/email-verification/verify",
            json={"email": email, "code": code},
            expected_status=200,
            auth=AuthContext.PUBLIC,
        )

    async def login(self, email: str, password: str) -> tuple[str, int]:
        """Authenticate a verified user and return its human JWT details."""
        response = await self._request(
            "POST",
            "/auth/login",
            json={"email": email, "password": password},
            expected_status=200,
            auth=AuthContext.PUBLIC,
        )
        data = self._response_data(response)
        try:
            return str(data["accessToken"]), int(data["expiresIn"])
        except (KeyError, TypeError, ValueError) as err:
            raise FluksApiError("INVALID_RESPONSE") from err

    async def list_sites(self) -> list[dict[str, Any]]:
        """List Sites owned by the authenticated human user."""
        response = await self._request(
            "GET", "/sites", expected_status=200, auth=AuthContext.HUMAN
        )
        data = self._response_data(response)
        if not isinstance(data, list):
            raise FluksApiError("INVALID_RESPONSE")
        return data

    async def create_site(
        self,
        name: str,
        latitude: float,
        longitude: float,
        timezone: str,
        energy_profile: str,
    ) -> dict[str, Any]:
        """Create a Site owned by the authenticated human user."""
        response = await self._request(
            "POST",
            "/sites",
            json={
                "name": name,
                "location": {"latitude": latitude, "longitude": longitude},
                "timezone": timezone,
                "energyProfile": energy_profile,
            },
            expected_status=201,
            auth=AuthContext.HUMAN,
        )
        return self._response_object(response)

    async def delete_site(self, site_id: str) -> None:
        """Lifecycle-delete one Site using explicit human authentication."""
        await self._request(
            "DELETE",
            f"/sites/{site_id}",
            expected_status=204,
            auth=AuthContext.HUMAN,
            response_body_required=False,
        )

    async def list_integrations(self, site_id: str) -> list[dict[str, Any]]:
        """List Integrations registered in an owned Site."""
        response = await self._request(
            "GET",
            f"/sites/{site_id}/integrations",
            expected_status=200,
            auth=AuthContext.HUMAN,
        )
        data = self._response_data(response)
        if not isinstance(data, list):
            raise FluksApiError("INVALID_RESPONSE")
        return data

    async def create_integration(
        self, site_id: str, integration_id: str
    ) -> dict[str, Any]:
        """Register this Home Assistant installation in an owned Site."""
        response = await self._request(
            "POST",
            f"/sites/{site_id}/integrations",
            json={"integrationId": integration_id, "type": INTEGRATION_TYPE},
            expected_status=201,
            auth=AuthContext.HUMAN,
        )
        integration = self._response_object(response)
        self._integration_key_from_response(integration)
        return integration

    async def issue_integration_key(
        self, site_id: str, integration_internal_id: str
    ) -> str:
        """Issue or rotate the machine key for an existing Integration."""
        response = await self._request(
            "POST",
            f"/sites/{site_id}/integrations/{integration_internal_id}/integration-key",
            expected_status=201,
            auth=AuthContext.HUMAN,
        )
        data = self._response_object(response)
        return self._integration_key_from_response(data)

    async def list_devices(self, site_id: str) -> list[dict[str, Any]]:
        """List canonical Devices in an owned Site."""
        response = await self._request(
            "GET",
            f"/sites/{site_id}/devices",
            expected_status=200,
            auth=AuthContext.INTEGRATION,
        )
        data = self._response_data(response)
        if not isinstance(data, list):
            raise FluksApiError("INVALID_RESPONSE")
        return data

    async def create_device(
        self,
        site_id: str,
        device_id: str,
        device_type: str,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a canonical Device using its stable external identity."""
        payload: dict[str, Any] = {"deviceId": device_id, "type": device_type}
        if properties:
            payload["properties"] = properties
        response = await self._request(
            "POST",
            f"/sites/{site_id}/devices",
            json=payload,
            expected_status=201,
            auth=AuthContext.INTEGRATION,
        )
        return self._response_object(response)

    async def get_device(
        self, site_id: str, device_internal_id: str
    ) -> dict[str, Any]:
        """Get one Site-owned Device by its immutable internal identity."""
        response = await self._request(
            "GET",
            f"/sites/{site_id}/devices/{device_internal_id}",
            expected_status=200,
            auth=AuthContext.INTEGRATION,
        )
        return self._response_object(response)

    async def update_device_properties(
        self,
        site_id: str,
        device_internal_id: str,
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        """Incrementally add, replace, or clear documented Device properties."""
        response = await self._request(
            "PATCH",
            f"/sites/{site_id}/devices/{device_internal_id}",
            json={"properties": properties},
            expected_status=200,
            auth=AuthContext.INTEGRATION,
        )
        return self._response_object(response)

    async def delete_device(
        self, site_id: str, device_internal_id: str
    ) -> None:
        """Lifecycle-delete one Device using explicit human authentication."""
        await self._request(
            "DELETE",
            f"/sites/{site_id}/devices/{device_internal_id}",
            expected_status=204,
            auth=AuthContext.HUMAN,
            response_body_required=False,
        )

    async def list_mappings(
        self, site_id: str, *, device_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List Integration-specific Mappings in an owned Site."""
        response = await self._request(
            "GET",
            f"/sites/{site_id}/mappings",
            params={"deviceId": device_id} if device_id else None,
            expected_status=200,
            auth=AuthContext.INTEGRATION,
        )
        data = self._response_data(response)
        if not isinstance(data, list):
            raise FluksApiError("INVALID_RESPONSE")
        return data

    async def create_mapping(
        self, site_id: str, mapping: dict[str, Any]
    ) -> dict[str, Any]:
        """Create one documented Integration-specific canonical Mapping."""
        response = await self._request(
            "POST",
            f"/sites/{site_id}/mappings",
            json=mapping,
            expected_status=201,
            auth=AuthContext.INTEGRATION,
        )
        return self._response_object(response)

    async def update_mapping(
        self,
        site_id: str,
        mapping_id: str,
        configuration: dict[str, Any],
        *,
        value_condition: str | None | object = _UNSET,
    ) -> dict[str, Any]:
        """Replace one Mapping's configuration and optional value condition."""
        payload: dict[str, Any] = {"configuration": configuration}
        if value_condition is not _UNSET:
            payload["valueCondition"] = value_condition
        response = await self._request(
            "PATCH",
            f"/sites/{site_id}/mappings/{mapping_id}",
            json=payload,
            expected_status=200,
            auth=AuthContext.INTEGRATION,
        )
        return self._response_object(response)

    async def delete_mapping(self, site_id: str, mapping_id: str) -> None:
        """Remove one Mapping without deleting its Device or other Mappings."""
        await self._request(
            "DELETE",
            f"/sites/{site_id}/mappings/{mapping_id}",
            expected_status=204,
            auth=AuthContext.INTEGRATION,
            response_body_required=False,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        expected_status: int,
        auth: AuthContext,
        response_body_required: bool = True,
    ) -> dict[str, Any]:
        headers = self._authorization_headers(auth)

        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                params=params,
                headers=headers,
                timeout=ClientTimeout(total=API_TIMEOUT_SECONDS),
            ) as response:
                if response.status == expected_status and not response_body_required:
                    body: Any = {}
                else:
                    try:
                        body = await response.json()
                    except (ClientError, ValueError) as err:
                        raise FluksApiError("INVALID_RESPONSE") from err
        except (ClientError, asyncio.TimeoutError) as err:
            raise FluksCannotConnect from err

        if response.status == expected_status:
            if not isinstance(body, dict):
                raise FluksApiError("INVALID_RESPONSE")
            return body

        code = self._error_code(body)
        if code == "INVALID_CREDENTIALS":
            raise FluksInvalidCredentials(code)
        if code == "INVALID_VERIFICATION_CODE":
            raise FluksInvalidVerificationCode(code)
        if code == "VALIDATION_ERROR":
            raise FluksValidationError(code)
        if response.status == 401 or code == "UNAUTHORIZED":
            raise FluksUnauthorized(code)
        if response.status == 409:
            raise FluksConflict(code)
        if response.status == 404:
            raise FluksNotFound(code)
        raise FluksApiError(code)

    def _authorization_headers(self, auth: AuthContext) -> dict[str, str]:
        """Build Authorization only from the deliberately selected context."""
        if auth is AuthContext.PUBLIC:
            return {}
        credential = (
            self._human_access_token
            if auth is AuthContext.HUMAN
            else self._integration_key
        )
        if credential is None:
            raise FluksUnauthorized("MISSING_CREDENTIAL")
        return {"Authorization": f"Bearer {credential}"}

    @staticmethod
    def _integration_key_from_response(data: dict[str, Any]) -> str:
        """Validate the documented one-time plaintext credential response."""
        integration_key = data.get("integrationKey")
        if not isinstance(integration_key, str) or not INTEGRATION_KEY_PATTERN.fullmatch(
            integration_key
        ):
            raise FluksApiError("INVALID_RESPONSE")
        return integration_key

    @staticmethod
    def _error_code(body: Any) -> str | None:
        if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
            return None
        code = body["error"].get("code")
        return code if isinstance(code, str) else None

    @staticmethod
    def _response_data(body: dict[str, Any]) -> Any:
        if "data" not in body:
            raise FluksApiError("INVALID_RESPONSE")
        return body["data"]

    @classmethod
    def _response_object(cls, body: dict[str, Any]) -> dict[str, Any]:
        data = cls._response_data(body)
        if not isinstance(data, dict):
            raise FluksApiError("INVALID_RESPONSE")
        return data
