#!/usr/bin/env python3
"""One test group per mapping rule MAP-01..MAP-18 (MRC-CHT-HD-01 section 13.4)."""

import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "integration" / "rc-sync"))

from rc_sync.mapping import (  # noqa: E402
    OrgMap,
    OrgMapRow,
    avatar_to_set,
    classify_link_conflict,
    load_spec,
    map_user,
    should_write_language,
    split_full_name,
)

NOW = datetime(2026, 11, 5, 8, 0, tzinfo=timezone.utc)

ORG_MAP = OrgMap([
    OrgMapRow("KHOI-VP", "announce", "tb-khoi-van-phong", "Khối Văn phòng"),
    OrgMapRow("PB-KT", "team", "pb-ke-toan", "Phòng Kế toán"),
    OrgMapRow("KT-CN", "team_channel", "kt-to-cong-no", "Tổ Công nợ", parent_team_code="PB-KT"),
    OrgMapRow("KT-TT", "team_channel", "kt-to-thanh-toan", "Tổ Thanh toán", parent_team_code="PB-KT"),
    OrgMapRow("KT-NHO", "merge", merge_into="KT-CN"),
    OrgMapRow("KHONG", "none"),
    OrgMapRow("GIAITHE", "team", "pb-cu", "Phòng cũ", status="inactive"),
    OrgMapRow("TUDO", "team", "pb-tu-do", "Phòng tự quản", managed=False),
])


def kc_user(**overrides):
    user = {
        "id": "b0d674bf-a770-468d-99e1-a308439e9aa8",
        "username": "ngoc.tb",
        "firstName": "Bảo Ngọc",
        "lastName": "Trần",
        "email": "ngoc.tb@miraclegroup.vn",
        "emailVerified": True,
        "enabled": True,
        "attributes": {
            "full_name": ["Trần Bảo Ngọc"],
            "employee_id": ["MRC-0142"],
            "org_unit_code": ["KT-CN"],
            "job_title": ["Kế toán công nợ"],
            "locale": ["vi"],
            "account_type": ["employee"],
        },
    }
    attributes = overrides.pop("attributes", None)
    user.update(overrides)
    if attributes is not None:
        user["attributes"] = {**user["attributes"], **attributes}
    return user


def mapped(user=None, groups=(), roles=()):
    return map_user(user or kc_user(), groups, roles, ORG_MAP, NOW)


class TestMap01IdentityLink:
    def test_keeps_immutable_and_business_keys(self):
        profile = mapped()
        assert profile.kc_user_id == "b0d674bf-a770-468d-99e1-a308439e9aa8"
        assert profile.employee_id == "MRC-0142"

    def test_employee_id_may_be_missing(self):
        assert mapped(kc_user(attributes={"employee_id": []})).employee_id is None


class TestMap02Map07Username:
    def test_canonical_username_passes_through(self):
        assert mapped().username == "ngoc.tb"

    @pytest.mark.parametrize("bad", ["Đặng Thu Phương", "Ngoc.TB", "a..b", ".abc", "a" * 33])
    def test_non_canonical_username_is_rejected_not_rewritten(self, bad):
        with pytest.raises(ValueError, match="username_not_canonical"):
            mapped(kc_user(username=bad))

    def test_missing_username_is_rejected(self):
        with pytest.raises(ValueError, match="username_not_canonical"):
            mapped(kc_user(username=None))


class TestMap03Name:
    def test_uses_full_name_verbatim_in_vietnamese_order(self):
        assert mapped().name == "Trần Bảo Ngọc"

    def test_normalizes_to_nfc_and_collapses_spaces(self):
        decomposed = unicodedata.normalize("NFD", "  Trần   Bảo  Ngọc ")
        name = mapped(kc_user(attributes={"full_name": [decomposed]})).name
        assert name == "Trần Bảo Ngọc"
        assert unicodedata.is_normalized("NFC", name)

    @pytest.mark.parametrize("empty", [[], [""], ["   "]])
    def test_falls_back_to_last_then_first_name(self, empty):
        assert mapped(kc_user(attributes={"full_name": empty})).name == "Trần Bảo Ngọc"


class TestMap04SplitName:
    def test_first_word_is_the_family_name(self):
        assert split_full_name("Trần Bảo Ngọc") == {"lastName": "Trần", "firstName": "Bảo Ngọc"}

    def test_single_word_and_empty(self):
        assert split_full_name("Khoa") == {"lastName": "Khoa", "firstName": ""}
        assert split_full_name("") == {"lastName": "", "firstName": ""}


class TestMap05Email:
    def test_lowercased_and_stripped(self):
        assert mapped(kc_user(email="  Ngoc.TB@MiracleGroup.vn ")).email == "ngoc.tb@miraclegroup.vn"

    def test_verified_follows_keycloak(self):
        assert mapped().verified is True
        assert mapped(kc_user(emailVerified=False)).verified is False


class TestMap06MissingEmail:
    @pytest.mark.parametrize("email", [None, "", "   "])
    def test_uses_undeliverable_placeholder(self, email):
        profile = mapped(kc_user(email=email))
        assert profile.email == "ngoc.tb@noemail.miraclegroup.vn"
        assert profile.verified is False
        assert profile.email_is_placeholder is True

    def test_real_email_is_not_a_placeholder(self):
        assert mapped().email_is_placeholder is False


class TestMap08Conflicts:
    KC_ID = "kc-1"

    def test_no_conflict_when_nothing_exists(self):
        assert classify_link_conflict(self.KC_ID, None, None, None) is None

    def test_no_conflict_for_own_linked_account(self):
        own = {"_id": "rc-1", "services": {"keycloak": {"id": self.KC_ID}}}
        assert classify_link_conflict(self.KC_ID, own, own, "rc-1") is None

    def test_prelinked_account_without_service_is_not_a_conflict(self):
        own = {"_id": "rc-1"}
        assert classify_link_conflict(self.KC_ID, own, own, "rc-1") is None

    def test_same_username_on_unlinked_account(self):
        assert classify_link_conflict(self.KC_ID, {"_id": "rc-9"}, None, None) == "username_conflict"

    def test_same_email_on_another_account(self):
        assert classify_link_conflict(self.KC_ID, None, {"_id": "rc-9"}, "rc-1") == "email_conflict"

    def test_linked_account_carrying_another_sub_is_a_security_alert(self):
        hijacked = {"_id": "rc-1", "services": {"keycloak": {"id": "other-sub"}}}
        assert classify_link_conflict(self.KC_ID, hijacked, None, "rc-1") == "link_mismatch"


class TestMap09Active:
    def test_enabled_without_expiry(self):
        assert mapped().active is True

    def test_disabled_in_keycloak(self):
        assert mapped(kc_user(enabled=False)).active is False

    def test_expired_and_not_yet_expired(self):
        past = (NOW - timedelta(days=1)).isoformat()
        future = (NOW + timedelta(days=1)).isoformat()
        assert mapped(kc_user(attributes={"expires_at": [past]})).active is False
        assert mapped(kc_user(attributes={"expires_at": [future]})).active is True

    def test_unreadable_expiry_fails_closed(self):
        profile = mapped(kc_user(attributes={"expires_at": ["không phải ngày"]}))
        assert profile.active is False
        assert "invalid_expires_at" in profile.warnings


class TestMap10AccountType:
    def test_service_accounts_are_not_provisioned(self):
        assert mapped(kc_user(attributes={"account_type": ["service"]})) is None

    @pytest.mark.parametrize("account_type", ["employee", "contractor", "privileged"])
    def test_people_get_the_user_role(self, account_type):
        assert mapped(kc_user(attributes={"account_type": [account_type]})).roles == ["user"]


class TestMap11Rooms:
    def test_team_channel_also_brings_its_team(self):
        assert mapped(groups=["/org/khoi-vp/pb-kt/KT-CN"]).rooms == {"kt-to-cong-no", "pb-ke-toan"}

    def test_secondary_groups_count_too(self):
        rooms = mapped(groups=["/org/x/KT-CN", "/org/x/KT-TT", "/org/KHOI-VP"]).rooms
        assert rooms == {"kt-to-cong-no", "kt-to-thanh-toan", "pb-ke-toan", "tb-khoi-van-phong"}

    def test_merge_follows_target_and_none_is_skipped(self):
        assert mapped(groups=["/org/x/KT-NHO", "/org/KHONG"]).rooms == {"kt-to-cong-no", "pb-ke-toan"}

    def test_unknown_inactive_and_non_org_groups_are_ignored(self):
        assert mapped(groups=["/org/LA", "/org/GIAITHE", "/func/chat-admin"]).rooms == set()

    def test_merge_cycle_does_not_hang(self):
        loop = OrgMap([OrgMapRow("A", "merge", merge_into="B"), OrgMapRow("B", "merge", merge_into="A")])
        assert loop.target_rooms(["/org/A"]) == set()

    def test_removes_only_managed_rooms(self):
        target = {"pb-ke-toan"}
        current = ["pb-ke-toan", "kt-to-cong-no", "pb-tu-do", "room-tu-tao"]
        assert ORG_MAP.rooms_to_remove(current, target) == {"kt-to-cong-no"}


class TestMap12Roles:
    def test_chat_admin_role(self):
        assert mapped(roles=["rc-admin"]).roles == ["user", "admin"]

    def test_no_client_role_means_plain_user(self):
        assert mapped().roles == ["user"]

    def test_auditor_is_unmapped_on_community_and_reported(self):
        profile = mapped(roles=["rc-auditor"])
        assert profile.roles == ["user"]
        assert "role_not_mapped:rc-auditor" in profile.warnings

    def test_unrelated_roles_are_ignored(self):
        assert mapped(roles=["uma_authorization"]).roles == ["user"]


class TestMap13RoomRoles:
    def test_head_owns_and_deputy_moderates_the_team(self):
        assert ORG_MAP.room_roles(["PB-KT"], []) == {"pb-ke-toan": "owner"}
        assert ORG_MAP.room_roles([], ["PB-KT"]) == {"pb-ke-toan": "moderator"}

    def test_head_wins_over_deputy_of_same_unit(self):
        assert ORG_MAP.room_roles(["PB-KT"], ["PB-KT"]) == {"pb-ke-toan": "owner"}

    def test_units_that_are_not_teams_give_no_role(self):
        assert ORG_MAP.room_roles(["KT-CN", "KHONG"], []) == {}


class TestMap14Language:
    @pytest.mark.parametrize("locale, expected", [("vi", "vi-VN"), ("km", "km"), ("en", "en"), ("fr", "vi-VN"), (None, "vi-VN")])
    def test_lookup_with_vietnamese_default(self, locale, expected):
        assert mapped(kc_user(attributes={"locale": [locale]})).language == expected

    def test_user_owns_language_after_first_write(self):
        assert should_write_language(is_create=True, user_changed_language=False) is True
        assert should_write_language(is_create=False, user_changed_language=False) is True
        assert should_write_language(is_create=False, user_changed_language=True) is False


class TestMap15EmployeeId:
    def test_copied_verbatim(self):
        assert mapped().custom_fields["employee_id"] == "MRC-0142"

    def test_empty_when_missing(self):
        assert mapped(kc_user(attributes={"employee_id": []})).custom_fields["employee_id"] == ""


class TestMap16OrgAndTitle:
    def test_primary_unit_name_comes_from_org_map(self):
        assert mapped().custom_fields["org_unit"] == "Tổ Công nợ"

    def test_job_title_copied(self):
        assert mapped().custom_fields["job_title"] == "Kế toán công nợ"

    def test_unknown_unit_is_empty_and_secondary_groups_do_not_change_it(self):
        assert mapped(kc_user(attributes={"org_unit_code": ["LA"]})).custom_fields["org_unit"] == ""
        assert mapped(groups=["/org/x/KT-TT"]).custom_fields["org_unit"] == "Tổ Công nợ"


class TestMap17UserOwnedFields:
    def test_bio_and_nickname_are_never_written(self):
        data = mapped(kc_user(bio="x", nickname="y")).user_data()
        assert "bio" not in data and "nickname" not in data


class TestMap18Avatar:
    def test_set_once_at_creation_only(self):
        assert avatar_to_set(True, "https://crm/a.png") == "https://crm/a.png"
        assert avatar_to_set(False, "https://crm/a.png") is None
        assert avatar_to_set(True, None) is None


class TestProfileHash:
    def test_same_input_gives_same_hash_regardless_of_room_order(self):
        first = mapped(groups=["/org/x/KT-CN", "/org/x/KT-TT"])
        second = mapped(groups=["/org/x/KT-TT", "/org/x/KT-CN"])
        assert first.profile_hash() == second.profile_hash()

    @pytest.mark.parametrize("changed", [
        {"email": "khac@miraclegroup.vn"},
        {"enabled": False},
        {"attributes": {"full_name": ["Trần Bảo Ngọc Anh"]}},
    ])
    def test_any_owned_change_alters_the_hash(self, changed):
        assert mapped(kc_user(**changed)).profile_hash() != mapped().profile_hash()


class TestSpecFile:
    def test_every_rule_with_code_is_declared(self):
        fields = load_spec()["fields"]
        assert {"username", "name", "email", "verified", "active", "roles", "preferences.language"} <= set(fields)

    def test_yaml_cannot_contain_executable_expressions(self):
        assert not [name for name, rule in load_spec()["fields"].items() if "expr" in rule]
