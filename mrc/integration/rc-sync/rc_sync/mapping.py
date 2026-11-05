"""Keycloak -> Rocket.Chat mapping engine (MAP-01..MAP-18).

Everything here is pure: no network, no database, and the current time is passed
in. Target state is computed first and compared afterwards, which is what makes a
sync safe to repeat (section 12.6).
"""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

import yaml

from .username import assert_canonical_username

DEFAULT_SPEC_PATH = Path(__file__).resolve().parent.parent / "mapping" / "rocketchat.yaml"


def _assert_canonical(value: str) -> str:
    assert_canonical_username(value)
    return value


TRANSFORMS: Dict[str, Callable[[str], str]] = {
    "nfc": lambda v: unicodedata.normalize("NFC", v),
    "strip": str.strip,
    "lower": str.lower,
    "collapse_spaces": lambda v: " ".join(v.split()),
    "assert_canonical_username": _assert_canonical,
}


def load_spec(path: Path = DEFAULT_SPEC_PATH) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass(frozen=True)
class OrgMapRow:
    """One row of iam.rc_org_map (section 13.6)."""

    org_unit_code: str
    mode: str
    rc_room_name: Optional[str] = None
    rc_display_name: Optional[str] = None
    parent_team_code: Optional[str] = None
    merge_into: Optional[str] = None
    managed: bool = True
    status: str = "active"


class OrgMap:
    """Projects the five-level org tree onto flat Rocket.Chat rooms (MAP-11)."""

    def __init__(self, rows: Iterable[OrgMapRow] = ()):
        self._rows = {row.org_unit_code: row for row in rows}

    @staticmethod
    def group_to_code(group_path: str) -> str:
        """The org unit code is the last segment of the Keycloak group path."""
        return group_path.rstrip("/").rsplit("/", 1)[-1]

    def _resolve(self, code: Optional[str]) -> Optional[OrgMapRow]:
        """Follow merge_into to the row that actually owns a room."""
        seen: Set[str] = set()
        while code and code not in seen:
            seen.add(code)
            row = self._rows.get(code)
            if row is None or row.status != "active" or row.mode == "none":
                return None
            if row.mode != "merge":
                return row
            code = row.merge_into
        return None

    def target_rooms(self, groups: Iterable[str]) -> Set[str]:
        """Rooms a person belongs in, for primary and secondary groups alike."""
        rooms: Set[str] = set()
        for group in groups:
            row = self._resolve(self.group_to_code(group))
            if row is None or not row.rc_room_name:
                continue
            rooms.add(row.rc_room_name)
            if row.mode == "team_channel":
                # A channel inside a Team is only reachable by members of that Team.
                team = self._resolve(row.parent_team_code)
                if team is not None and team.rc_room_name:
                    rooms.add(team.rc_room_name)
        return rooms

    def rooms_to_remove(self, current_rooms: Iterable[str], target_rooms: Set[str]) -> Set[str]:
        """Rooms to leave: only managed rooms, so hand-joined rooms are never touched."""
        managed = {r.rc_room_name for r in self._rows.values() if r.managed and r.rc_room_name}
        return (set(current_rooms) & managed) - target_rooms

    def unit_name(self, code: Optional[str]) -> Optional[str]:
        row = self._rows.get(code) if code else None
        return row.rc_display_name if row else None

    def room_roles(self, headed_units: Iterable[str], deputy_units: Iterable[str]) -> Dict[str, str]:
        """MAP-13: the unit head owns the Team of that unit, the deputy moderates it."""
        roles: Dict[str, str] = {}
        for units, role in ((deputy_units, "moderator"), (headed_units, "owner")):
            for code in units:
                row = self._resolve(code)
                if row is not None and row.mode == "team" and row.rc_room_name:
                    roles[row.rc_room_name] = role
        return roles


@dataclass
class TargetProfile:
    """The Rocket.Chat state a person should have, plus keys for the crosswalk."""

    kc_user_id: str
    employee_id: Optional[str]
    username: str
    name: str
    email: str
    verified: bool
    active: bool
    roles: List[str]
    custom_fields: Dict[str, str]
    language: str
    rooms: Set[str] = field(default_factory=set)
    email_is_placeholder: bool = False
    warnings: List[str] = field(default_factory=list)

    def user_data(self) -> Dict[str, Any]:
        """Fields rc-sync may write. bio and nickname belong to the user (MAP-17)."""
        return {
            "username": self.username,
            "name": self.name,
            "email": self.email,
            "verified": self.verified,
            "active": self.active,
            "roles": self.roles,
            "customFields": self.custom_fields,
        }

    def profile_hash(self) -> str:
        """SHA-256 of the canonical target state, stored in iam.rc_link."""
        canonical = {**self.user_data(), "language": self.language, "rooms": sorted(self.rooms)}
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _first(value: Any) -> Any:
    """Keycloak returns attributes as lists; the mapping works with single values."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _read_path(source: Dict[str, Any], path: str) -> Any:
    value: Any = source
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _render(template: str, source: Dict[str, Any]) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(source.get(m[1]) or ""), template)


def _apply_transforms(value: str, names: Iterable[str]) -> str:
    for name in names:
        value = TRANSFORMS[name](value)
    return value


def _parse_expiry(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_active(kc_user: Dict[str, Any], now: datetime, warnings: List[str]) -> bool:
    """MAP-09. An unreadable expiry date counts as expired, because access must fail closed."""
    attributes = kc_user.get("attributes") or {}
    if _first(attributes.get("account_type")) == "service" or not kc_user.get("enabled"):
        return False
    try:
        expires = _parse_expiry(_first(attributes.get("expires_at")))
    except ValueError:
        warnings.append("invalid_expires_at")
        return False
    return expires is None or expires > now


def email_verified(kc_user: Dict[str, Any]) -> bool:
    return bool((kc_user.get("email") or "").strip()) and bool(kc_user.get("emailVerified"))


def should_skip(kc_user: Dict[str, Any], spec: Dict[str, Any]) -> bool:
    """MAP-10: service accounts never get a chat account."""
    for path, values in (spec.get("skip_if") or {}).items():
        if _first(_read_path(kc_user, path)) in values:
            return True
    return False


def _map_field(rule: Dict[str, Any], kc_user: Dict[str, Any]) -> Any:
    value = _first(_read_path(kc_user, rule["from"]))
    if not (isinstance(value, str) and value.strip()):
        value = _render(rule["fallback"], kc_user).strip() if "fallback" in rule else None
    if value:
        return _apply_transforms(value, rule.get("transform", []))
    return None


def _map_roles(rule: Dict[str, Any], client_roles: Iterable[str], warnings: List[str]) -> List[str]:
    roles = list(rule.get("base", []))
    lookup = rule.get("lookup", {})
    for role in client_roles:
        if role not in lookup:
            continue
        mapped = lookup[role]
        if mapped is None:
            warnings.append(f"role_not_mapped:{role}")
        elif mapped not in roles:
            roles.append(mapped)
    return roles


def map_user(
    kc_user: Dict[str, Any],
    kc_groups: Iterable[str],
    kc_client_roles: Iterable[str],
    org_map: OrgMap,
    now: datetime,
    spec: Optional[Dict[str, Any]] = None,
) -> Optional[TargetProfile]:
    """Compute the target profile for one Keycloak user, or None when it must not be provisioned."""
    spec = spec or load_spec()
    if should_skip(kc_user, spec):
        return None

    fields = spec["fields"]
    warnings: List[str] = []

    username = _map_field(fields["username"], kc_user)
    if username is None:
        _assert_canonical("")

    email = _map_field(fields["email"], kc_user)
    email_is_placeholder = email is None
    if email_is_placeholder:
        email = _render(fields["email"]["when_missing"], {"username": username})

    attributes = kc_user.get("attributes") or {}
    custom_fields = {
        "employee_id": _first(attributes.get("employee_id")) or fields["customFields.employee_id"]["default"],
        "org_unit": org_map.unit_name(_first(attributes.get("org_unit_code"))) or fields["customFields.org_unit"]["default"],
        "job_title": _first(attributes.get("job_title")) or fields["customFields.job_title"]["default"],
    }

    language_rule = fields["preferences.language"]
    language = language_rule["lookup"].get(_first(attributes.get("locale")), language_rule["default"])

    memberships = spec["memberships"]
    return TargetProfile(
        kc_user_id=kc_user["id"],
        employee_id=_first(attributes.get("employee_id")),
        username=username,
        name=_map_field(fields["name"], kc_user) or username,
        email=email,
        verified=email_verified(kc_user),
        active=is_active(kc_user, now, warnings),
        roles=_map_roles(fields["roles"], kc_client_roles, warnings),
        custom_fields=custom_fields,
        language=language,
        rooms=org_map.target_rooms(g for g in kc_groups if g.startswith(memberships["prefix"])),
        email_is_placeholder=email_is_placeholder,
        warnings=warnings,
    )


def split_full_name(full_name: str) -> Dict[str, str]:
    """MAP-04, first leg (CRM -> Keycloak): for Keycloak screens only, never the source of the chat name."""
    parts = unicodedata.normalize("NFC", full_name).split()
    return {"lastName": parts[0] if parts else "", "firstName": " ".join(parts[1:])}


def should_write_language(is_create: bool, user_changed_language: bool) -> bool:
    """MAP-14: the user owns the language after the first write."""
    return is_create or not user_changed_language


def avatar_to_set(is_create: bool, crm_avatar_url: Optional[str]) -> Optional[str]:
    """MAP-18: seed the avatar once at creation; afterwards it belongs to the user."""
    return crm_avatar_url if is_create else None


def classify_link_conflict(
    kc_user_id: str,
    rc_user_by_username: Optional[Dict[str, Any]],
    rc_user_by_email: Optional[Dict[str, Any]],
    linked_rc_user_id: Optional[str],
) -> Optional[str]:
    """MAP-08 and section 13.7: conflicts are reported for a person to resolve, never auto-merged."""
    for rc_user in (rc_user_by_username, rc_user_by_email):
        service_id = _read_path(rc_user or {}, "services.keycloak.id")
        if rc_user and linked_rc_user_id == rc_user.get("_id") and service_id and service_id != kc_user_id:
            return "link_mismatch"
    if rc_user_by_username and rc_user_by_username.get("_id") != linked_rc_user_id:
        return "username_conflict"
    if rc_user_by_email and rc_user_by_email.get("_id") != linked_rc_user_id:
        return "email_conflict"
    return None
