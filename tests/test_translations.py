"""Tests for fluks milestone 1 translations."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from custom_components.fluks.const import DOMAIN

TRANSLATIONS = Path("custom_components") / DOMAIN / "translations"


def load_translation(language: str) -> dict:
    """Load a custom integration translation file."""
    return json.loads((TRANSLATIONS / f"{language}.json").read_text())


def key_structure(value, prefix=()):
    """Return every nested translation key path."""
    paths = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = (*prefix, key)
            paths.add(path)
            paths.update(key_structure(child, path))
    return paths


def string_literals(path: Path) -> set[str]:
    """Return string literals from a Python source file."""
    tree = ast.parse(path.read_text())
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_translation_files_are_valid_and_have_key_parity():
    """English and Danish contain identical translation structures."""
    english = load_translation("en")
    danish = load_translation("da")

    assert key_structure(english) == key_structure(danish)
    assert english["config"]["step"]["user"]["title"] == (
        "Your energy. Decides together."
    )
    assert danish["config"]["step"]["user"]["title"] == (
        "Your energy. Decides together."
    )


def test_config_flow_translation_keys_exist():
    """Every step, error, and abort key used by the flow is translated."""
    english = load_translation("en")["config"]
    source_literals = string_literals(
        Path("custom_components") / DOMAIN / "config_flow.py"
    )

    expected_steps = {
        "user",
        "login",
        "register",
        "verification",
        "verify",
        "resend_verification",
        "verification_resent",
        "site_choice",
        "site",
        "create_site",
        "register_integration",
    }
    expected_errors = {
        "cannot_connect",
        "invalid_auth",
        "invalid_input",
        "invalid_verification_code",
        "invalid_verification_code_format",
        "resend_cannot_connect",
        "resend_invalid_input",
        "resend_unknown",
        "unauthorized",
        "unknown",
    }
    expected_aborts = {
        "already_configured",
        "missing_registration",
        "unauthorized",
        "unknown",
    }

    assert expected_steps <= english["step"].keys()
    assert expected_errors <= english["error"].keys()
    assert expected_aborts <= english["abort"].keys()
    assert (
        expected_steps | expected_errors | (expected_aborts - {"already_configured"})
        <= source_literals
    )
