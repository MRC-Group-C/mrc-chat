#!/usr/bin/env python3
"""Rules for granting the rc-sync service account its permissions and storing its token."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "integration" / "scripts"))

from setup_service_account import (  # noqa: E402
    SERVICE_PERMISSIONS,
    permissions_to_grant,
    upsert_env_values,
)


def test_grant_keeps_existing_roles_and_skips_already_granted():
    current = {"create-user": ["admin"], "create-team": ["admin", "bot"]}

    updates = permissions_to_grant(current, ["create-user", "create-team"], "bot")

    assert updates == [{"_id": "create-user", "roles": ["admin", "bot"]}]


def test_grant_rejects_permissions_missing_on_this_version():
    with pytest.raises(ValueError, match="not-a-permission"):
        permissions_to_grant({}, ["not-a-permission"], "bot")


def test_service_keeps_its_rate_limit_exemption():
    assert "api-bypass-rate-limit" in SERVICE_PERMISSIONS


def test_exemption_is_not_added_twice_when_bot_already_has_it():
    current = {"api-bypass-rate-limit": ["admin", "bot", "app"]}

    assert permissions_to_grant(current, ["api-bypass-rate-limit"], "bot") == []


def test_exemption_is_restored_when_an_admin_removed_it_from_bot():
    current = {"api-bypass-rate-limit": ["admin", "app"]}

    updates = permissions_to_grant(current, ["api-bypass-rate-limit"], "bot")

    assert updates == [{"_id": "api-bypass-rate-limit", "roles": ["admin", "app", "bot"]}]


def test_service_never_receives_admin_only_control_permissions():
    assert "access-permissions" not in SERVICE_PERMISSIONS
    assert "delete-user" not in SERVICE_PERMISSIONS


def test_upsert_replaces_existing_key_and_keeps_other_lines():
    content = "# comment\nA=1\nRC_SYNC_TOKEN=old\n"

    result = upsert_env_values(content, {"RC_SYNC_TOKEN": "new"})

    assert result == "# comment\nA=1\nRC_SYNC_TOKEN=new\n"


def test_upsert_appends_missing_key():
    assert upsert_env_values("A=1\n", {"B": "2"}) == "A=1\nB=2\n"


def test_upsert_keeps_token_characters_literal():
    assert upsert_env_values("T=x\n", {"T": r"a\1b"}) == "T=a\\1b\n"
