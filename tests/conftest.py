"""Shared test configuration for fluks."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading the fluks custom integration in tests."""
    yield
