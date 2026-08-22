"""Small asynchronous client for milestone 1 of the fluks API."""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import API_BASE_URL, API_TIMEOUT_SECONDS, INTEGRATION_TYPE


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


class FluksApiClient:
    """Access only the fluks operations needed during onboarding."""

    def __init__(
        self,
        session: ClientSession,
        access_token: str | None = None,
        *,
        base_url: str = API_BASE_URL,
    ) -> None:
        self._session = session
        self._access_token = access_token
        self._base_url = base_url.rstrip("/")

    def set_access_token(self, access_token: str) -> None:
        """Set the human JWT used by authenticated operations."""
        self._access_token = access_token

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
        await self._request("POST", "/users", json=payload, expected_status=202)

    async def verify_email(self, email: str, code: str) -> None:
        """Verify a pending user's email address."""
        await self._request(
            "POST",
            "/users/email-verification/verify",
            json={"email": email, "code": code},
            expected_status=200,
        )

    async def resend_verification(self, email: str) -> None:
        """Request another verification email for a pending user."""
        await self._request(
            "POST",
            "/users/email-verification/resend",
            json={"email": email},
            expected_status=202,
        )

    async def login(self, email: str, password: str) -> tuple[str, int]:
        """Authenticate a verified user and return its human JWT details."""
        response = await self._request(
            "POST",
            "/auth/login",
            json={"email": email, "password": password},
            expected_status=200,
        )
        data = self._response_data(response)
        try:
            return str(data["accessToken"]), int(data["expiresIn"])
        except (KeyError, TypeError, ValueError) as err:
            raise FluksApiError("INVALID_RESPONSE") from err

    async def list_sites(self) -> list[dict[str, Any]]:
        """List Sites owned by the authenticated human user."""
        response = await self._request("GET", "/sites", expected_status=200)
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
        )
        return self._response_object(response)

    async def list_integrations(self, site_id: str) -> list[dict[str, Any]]:
        """List Integrations registered in an owned Site."""
        response = await self._request(
            "GET", f"/sites/{site_id}/integrations", expected_status=200
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
        )
        return self._response_object(response)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        expected_status: int,
    ) -> dict[str, Any]:
        headers = {}
        if self._access_token is not None:
            headers["Authorization"] = f"Bearer {self._access_token}"

        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                headers=headers,
                timeout=ClientTimeout(total=API_TIMEOUT_SECONDS),
            ) as response:
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
        raise FluksApiError(code)

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
