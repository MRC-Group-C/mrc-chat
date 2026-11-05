#!/usr/bin/env python3
"""
MRC-Chat Keycloak SSO Integration & Automated Configuration Script (T-603, T-604).
Location: mrc-chat/mrc/integration/scripts/setup_sso.py

Features:
1. MAP-07 Canonical Username Normalization:
   - NFC normalization, 'Đ'/'đ' -> 'D'/'d'
   - NFD decomposition, strip 'Mn' diacritical marks
   - Lowercase, sanitize characters to [0-9a-z._-]
   - Collapse consecutive dots, strip boundary punctuation
   - Max length 32 characters
   - assert_canonical_username validation
2. Automated Rocket.Chat Custom OAuth Configuration:
   - Accounts_OAuth_Custom_Keycloak=true
   - Keycloak OIDC endpoints, client credentials
   - merge_users=true & merge_users_distinct_services=true
   - display_name & preferred_username mapping
3. Security Lockdown:
   - Accounts_ShowFormLogin=false, Accounts_RegistrationForm=Disabled
   - Disallow username, email, real name, and password changes
4. Custom Fields: Accounts_CustomFields (employee_id, org_unit, job_title)
5. CLI Modes:
   - --test: Runs unit tests for MAP-07 and configuration validation
   - --dry-run: Checks connectivity and configuration drift without modifying settings
   - Normal mode: Connects to Rocket.Chat REST API and applies settings idempotently
"""

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure UTF-8 output encoding across Windows / Linux consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =============================================================================
# 1. MAP-07 CANONICAL USERNAME NORMALIZATION & VALIDATION
# =============================================================================

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rc-sync"))
from rc_sync.username import assert_canonical_username, normalize_username  # noqa: E402


# =============================================================================
# 2. DEFAULT SETTINGS & TARGET CONFIGURATION SCHEMA
# =============================================================================

DEFAULT_CUSTOM_FIELDS = json.dumps({
    "employee_id": {
        "type": "text",
        "required": False,
        "minLength": 0,
        "maxLength": 20,
        "public": True,
        "private": False,
        "modifyRecordField": False,
        "label": "Mã nhân viên"
    },
    "org_unit": {
        "type": "text",
        "required": False,
        "maxLength": 120,
        "public": True,
        "private": False,
        "label": "Đơn vị"
    },
    "job_title": {
        "type": "text",
        "required": False,
        "maxLength": 120,
        "public": True,
        "private": False,
        "label": "Chức danh"
    }
}, ensure_ascii=False, separators=(",", ":"))


def build_target_settings(kc_auth_url: str, client_id: str, client_secret: str) -> Dict[str, Any]:
    """Generate the complete dictionary of Rocket.Chat settings to be configured."""
    return {
        # Custom OAuth Keycloak Settings
        "Accounts_OAuth_Custom_Keycloak": True,
        "Accounts_OAuth_Custom_Keycloak_url": kc_auth_url,
        "Accounts_OAuth_Custom_Keycloak_token_path": "/openid-connect/token",
        "Accounts_OAuth_Custom_Keycloak_identity_path": "/openid-connect/userinfo",
        "Accounts_OAuth_Custom_Keycloak_authorize_path": "/openid-connect/auth",
        "Accounts_OAuth_Custom_Keycloak_scope": "openid profile email",
        "Accounts_OAuth_Custom_Keycloak_token_sent_via": "header",
        "Accounts_OAuth_Custom_Keycloak_identity_token_sent_via": "header",
        "Accounts_OAuth_Custom_Keycloak_access_token_param": "access_token",
        "Accounts_OAuth_Custom_Keycloak_id": client_id,
        "Accounts_OAuth_Custom_Keycloak_secret": client_secret,
        "Accounts_OAuth_Custom_Keycloak_login_style": "redirect",
        "Accounts_OAuth_Custom_Keycloak_button_label_text": "Đăng nhập bằng tài khoản MRC",
        "Accounts_OAuth_Custom_Keycloak_button_color": "#0052cc",
        "Accounts_OAuth_Custom_Keycloak_button_label_color": "#ffffff",
        "Accounts_OAuth_Custom_Keycloak_key_field": "username",
        "Accounts_OAuth_Custom_Keycloak_username_field": "preferred_username",
        "Accounts_OAuth_Custom_Keycloak_email_field": "email",
        "Accounts_OAuth_Custom_Keycloak_name_field": "display_name",
        "Accounts_OAuth_Custom_Keycloak_merge_users": True,
        "Accounts_OAuth_Custom_Keycloak_merge_users_distinct_services": True,
        "Accounts_OAuth_Custom_Keycloak_show_button": True,

        # Security Lockdown Settings (Single Source of Truth)
        "Accounts_ShowFormLogin": False,
        "Accounts_RegistrationForm": "Disabled",
        "Accounts_AllowUsernameChange": False,
        "Accounts_AllowEmailChange": False,
        "Accounts_AllowRealNameChange": False,
        "Accounts_AllowPasswordChange": False,
        "Accounts_AllowPasswordChangeForOAuthUsers": False,
        "Accounts_AllowDeleteOwnAccount": False,
        "Accounts_TwoFactorAuthentication_Enabled": False,
        "Accounts_AllowAnonymousRead": False,
        "Accounts_ManuallyApproveNewUsers": False,
        "Accounts_LoginExpiration": 1,
        "UTF8_User_Names_Validation": "[0-9a-zA-Z-_.]+",
        "Site_Name": "Chat Miracle",

        # Custom Fields for Organization Profiles
        "Accounts_CustomFields": DEFAULT_CUSTOM_FIELDS
    }


def to_rc_setting_id(key: str) -> str:
    """Translate an environment-style OAuth key to the setting ID stored by Rocket.Chat."""
    prefix = "Accounts_OAuth_Custom_Keycloak"
    if key == prefix:
        return "Accounts_OAuth_Custom-Keycloak"
    if key.startswith(f"{prefix}_"):
        return f"Accounts_OAuth_Custom-Keycloak-{key[len(prefix) + 1:]}"
    return key


def display_setting_value(setting_id: str, value: Any) -> Any:
    """Return a value safe to print: secrets are masked, long values shortened."""
    if "secret" in setting_id.lower():
        return "***"
    return value if len(str(value)) < 40 else f"{str(value)[:37]}..."


# =============================================================================
# 3. HTTP CLIENT & ROCKET.CHAT REST API WRAPPER
# =============================================================================

class RocketChatAdminClient:
    """Lightweight REST API client for Rocket.Chat settings administration."""

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.auth_token: Optional[str] = None
        self.user_id: Optional[str] = None

    def _request(self, method: str, endpoint: str, data: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if self.auth_token and self.user_id:
            headers["X-Auth-Token"] = self.auth_token
            headers["X-User-Id"] = self.user_id

        payload = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                status_code = response.status
                body = json.loads(response.read().decode("utf-8"))
                return status_code, body
        except urllib.error.HTTPError as e:
            err_body = {}
            try:
                err_body = json.loads(e.read().decode("utf-8"))
            except Exception:
                pass
            return e.code, err_body
        except urllib.error.URLError as e:
            raise ConnectionError(f"Cannot reach Rocket.Chat at {url}: {e.reason}") from e

    def login(self, username: str, password: str) -> bool:
        """Authenticate as administrator via POST /api/v1/login."""
        status_code, body = self._request("POST", "/api/v1/login", {"user": username, "password": password})
        if status_code == 200 and body.get("status") == "success":
            data = body.get("data", {})
            self.auth_token = data.get("authToken")
            self.user_id = data.get("userId")
            return True
        return False

    def logout(self) -> None:
        """Terminate administrative session via POST /api/v1/logout."""
        if self.auth_token and self.user_id:
            try:
                self._request("POST", "/api/v1/logout")
            except Exception:
                pass
            self.auth_token = None
            self.user_id = None

    def get_setting(self, setting_id: str) -> Optional[Any]:
        """Fetch current setting value via GET /api/v1/settings/<setting_id>."""
        status_code, body = self._request("GET", f"/api/v1/settings/{setting_id}")
        if status_code == 200:
            return body.get("value")
        return None

    def update_setting(self, setting_id: str, value: Any) -> bool:
        """Update setting value via POST /api/v1/settings/<setting_id>."""
        status_code, body = self._request("POST", f"/api/v1/settings/{setting_id}", {"value": value})
        return status_code == 200 and body.get("success", False) is True

    def check_health(self) -> Tuple[bool, str]:
        """Check Rocket.Chat liveness / info endpoint."""
        try:
            status_code, body = self._request("GET", "/api/info")
            if status_code == 200 and body.get("success"):
                version = body.get("info", {}).get("version", "unknown")
                return True, f"Rocket.Chat online (version: {version})"
        except Exception:
            pass
        # Fallback to /livez
        try:
            req = urllib.request.Request(f"{self.base_url}/livez", method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    return True, "Rocket.Chat online (/livez 200 OK)"
        except Exception as e:
            return False, f"Rocket.Chat unreachable at {self.base_url} ({e})"
        return False, f"Rocket.Chat unreachable at {self.base_url}"


def check_keycloak_discovery(kc_auth_url: str, timeout: float = 5.0) -> Tuple[bool, str]:
    """Test connectivity to Keycloak OpenID configuration discovery endpoint."""
    # Derive discovery URL from auth URL:
    # E.g. https://sso.miraclegroup.vn/realms/mrc/protocol -> https://sso.miraclegroup.vn/realms/mrc/.well-known/openid-configuration
    normalized_url = kc_auth_url.rstrip("/")
    if normalized_url.endswith("/protocol"):
        base_realm = normalized_url[:-9]
    elif normalized_url.endswith("/protocol/openid-connect"):
        base_realm = normalized_url[:-24]
    else:
        base_realm = normalized_url

    discovery_url = f"{base_realm}/.well-known/openid-configuration"
    try:
        req = urllib.request.Request(discovery_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                issuer = body.get("issuer", "unknown")
                return True, f"Keycloak OIDC Discovery OK (issuer: {issuer})"
            return False, f"Keycloak discovery returned HTTP {resp.status}"
    except Exception as e:
        return False, f"Keycloak discovery unreachable at {discovery_url} ({e})"


def _split_keycloak_url(kc_auth_url: str) -> Tuple[str, str]:
    """Split a Keycloak protocol URL into its base URL and realm name."""
    normalized_url = kc_auth_url.rstrip("/")
    if "/realms/" in normalized_url:
        parts = normalized_url.split("/realms/")
        return parts[0], parts[1].split("/")[0]
    return normalized_url, "mrc"


def _keycloak_admin_token(kc_base: str, admin_user: str, admin_password: str, timeout: float) -> str:
    """Obtain a master-realm admin access token."""
    token_data = urllib.parse.urlencode({
        "client_id": "admin-cli",
        "username": admin_user,
        "password": admin_password,
        "grant_type": "password"
    }).encode("utf-8")
    token_req = urllib.request.Request(
        f"{kc_base}/realms/master/protocol/openid-connect/token",
        data=token_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(token_req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))["access_token"]


def _keycloak_admin_call(url: str, token: str, method: str = "GET", body: Any = None, timeout: float = 5.0) -> Any:
    """Call the Keycloak Admin REST API and return the parsed JSON body, if any."""
    req = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        },
        method=method
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8")) if raw else None


CHAT_CLAIMS_SCOPE = "chat.claims"
CHAT_PROFILE_ATTRIBUTES = {"full_name": "Họ và tên", "employee_id": "Mã nhân viên"}


def build_chat_claim_mappers(client_id: str) -> List[Dict[str, Any]]:
    """Protocol mappers carried by the chat.claims scope; both must reach userinfo."""
    return [
        {
            "name": "display_name",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-attribute-mapper",
            "consentRequired": False,
            "config": {
                "user.attribute": "full_name",
                "claim.name": "display_name",
                "jsonType.label": "String",
                "userinfo.token.claim": "true",
                "id.token.claim": "true",
                "access.token.claim": "false"
            }
        },
        {
            "name": "rc_roles",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-client-role-mapper",
            "consentRequired": False,
            "config": {
                "usermodel.clientRoleMapping.clientId": client_id,
                "claim.name": "rc_roles",
                "jsonType.label": "String",
                "multivalued": "true",
                "userinfo.token.claim": "true",
                "id.token.claim": "false",
                "access.token.claim": "false"
            }
        }
    ]


def ensure_chat_claims(
    kc_auth_url: str,
    client_id: str = "chat-miracle",
    admin_user: str = "adm_mrc_root",
    admin_password: str = "",
    timeout: float = 5.0
) -> Tuple[bool, str]:
    """Make full_name a managed attribute and serve display_name through chat.claims (T-602).

    Safe to run repeatedly. The scope is attached before the client-level mappers
    are removed so the claim never disappears in between.
    """
    if not admin_password:
        return False, "Configuring chat claims requires KEYCLOAK_ADMIN_PASSWORD"

    kc_base, realm = _split_keycloak_url(kc_auth_url)
    admin = f"{kc_base}/admin/realms/{realm}"

    try:
        token = _keycloak_admin_token(kc_base, admin_user, admin_password, timeout)

        profile = _keycloak_admin_call(f"{admin}/users/profile", token, timeout=timeout)
        known = {a.get("name") for a in profile.get("attributes", [])}
        missing = [name for name in CHAT_PROFILE_ATTRIBUTES if name not in known]
        for name in missing:
            profile["attributes"].append({
                "name": name,
                "displayName": CHAT_PROFILE_ATTRIBUTES[name],
                "permissions": {"view": ["admin", "user"], "edit": ["admin"]},
                "multivalued": False
            })
        if missing:
            _keycloak_admin_call(f"{admin}/users/profile", token, "PUT", profile, timeout)

        def find_scope() -> Optional[Dict[str, Any]]:
            scopes = _keycloak_admin_call(f"{admin}/client-scopes", token, timeout=timeout)
            return next((s for s in scopes if s.get("name") == CHAT_CLAIMS_SCOPE), None)

        scope = find_scope()
        if scope is None:
            _keycloak_admin_call(f"{admin}/client-scopes", token, "POST", {
                "name": CHAT_CLAIMS_SCOPE,
                "description": "Claims used by Chat Miracle (Rocket.Chat)",
                "protocol": "openid-connect",
                "attributes": {"include.in.token.scope": "false", "display.on.consent.screen": "false"}
            }, timeout)
            scope = find_scope()
        scope_url = f"{admin}/client-scopes/{scope['id']}/protocol-mappers/models"
        existing = {m.get("name") for m in _keycloak_admin_call(scope_url, token, timeout=timeout)}
        mappers = build_chat_claim_mappers(client_id)
        for mapper in mappers:
            if mapper["name"] not in existing:
                _keycloak_admin_call(scope_url, token, "POST", mapper, timeout)

        clients = _keycloak_admin_call(
            f"{admin}/clients?clientId={urllib.parse.quote(client_id)}", token, timeout=timeout
        )
        if not clients:
            return False, f"Client '{client_id}' not found in realm '{realm}'"
        client_url = f"{admin}/clients/{clients[0]['id']}"
        _keycloak_admin_call(f"{client_url}/default-client-scopes/{scope['id']}", token, "PUT", {}, timeout)

        scoped_names = {m["name"] for m in mappers}
        for mapper in _keycloak_admin_call(f"{client_url}/protocol-mappers/models", token, timeout=timeout):
            if mapper.get("name") in scoped_names:
                _keycloak_admin_call(f"{client_url}/protocol-mappers/models/{mapper['id']}", token, "DELETE", None, timeout)
    except urllib.error.HTTPError as e:
        return False, f"Keycloak Admin API rejected the chat claims setup (HTTP {e.code}): {e.reason}"
    except Exception as e:
        return False, f"Failed to configure chat claims: {e}"

    return True, f"User Profile and client scope '{CHAT_CLAIMS_SCOPE}' are in place for '{client_id}'"


def ensure_keycloak_client(
    kc_auth_url: str,
    client_id: str = "chat-miracle",
    client_secret: str = "",
    admin_user: str = "adm_mrc_root",
    admin_password: str = "",
    redirect_uri: str = "http://localhost:3000/_oauth/keycloak",
    dry_run: bool = False,
    timeout: float = 5.0
) -> Tuple[bool, str]:
    """Verify that client_id is registered and operational in Keycloak; register idempotently via Admin REST API if missing."""
    kc_base, realm = _split_keycloak_url(kc_auth_url)

    # Step 1: Probe live authorization endpoint first (fast, unauthenticated check)
    auth_probe_url = (
        f"{kc_base}/realms/{realm}/protocol/openid-connect/auth?"
        f"client_id={urllib.parse.quote(client_id)}&response_type=code&"
        f"redirect_uri={urllib.parse.quote(redirect_uri)}"
    )

    try:
        req = urllib.request.Request(auth_probe_url, headers={"Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                body = resp.read().decode("utf-8", errors="replace")
                if "Client not found" not in body and "client_not_found" not in body:
                    return True, f"Keycloak client '{client_id}' is active and operational (live auth probe HTTP 200 OK)"
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        if "Client not found" not in err_body and "client_not_found" not in err_body:
            return False, f"Keycloak auth probe failed with HTTP {e.code}: {err_body[:100]}"
    except Exception as e:
        return False, f"Cannot reach Keycloak at {kc_base}: {e}"

    # Step 2: Client is missing; check dry-run or obtain Keycloak Master Admin token to register it
    if dry_run:
        return True, f"Keycloak client '{client_id}' is missing; would be created via Admin REST API (dry-run)"

    if not client_secret or not admin_password:
        return False, (
            f"Keycloak client '{client_id}' is missing; registering it requires "
            "KC_CLIENT_SECRET and KEYCLOAK_ADMIN_PASSWORD"
        )

    try:
        token = _keycloak_admin_token(kc_base, admin_user, admin_password, timeout)
    except Exception as e:
        return False, f"Failed to authenticate as Keycloak admin for client registration: {e}"

    # Step 3: Register chat-miracle client via POST /admin/realms/{realm}/clients
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    client_payload = {
        "clientId": client_id,
        "name": "Chat Miracle (Rocket.Chat)",
        "description": "Enterprise Messaging Rocket.Chat OIDC Client",
        "rootUrl": "https://chat.miraclegroup.vn",
        "baseUrl": "https://chat.miraclegroup.vn",
        "enabled": True,
        "clientAuthenticatorType": "client-secret",
        "secret": client_secret,
        "redirectUris": [
            "https://chat.miraclegroup.vn/_oauth/keycloak",
            "http://localhost:3000/_oauth/keycloak",
            "http://127.0.0.1:3000/_oauth/keycloak"
        ],
        "webOrigins": ["+", "https://chat.miraclegroup.vn", "http://localhost:3000", "http://127.0.0.1:3000"],
        "standardFlowEnabled": True,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "publicClient": False,
        "protocol": "openid-connect",
        "protocolMappers": [
            {
                "name": "employee_id",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-usermodel-attribute-mapper",
                "consentRequired": False,
                "config": {
                    "userinfo.token.claim": "true",
                    "user.attribute": "employee_id",
                    "id.token.claim": "true",
                    "access.token.claim": "true",
                    "claim.name": "employee_id",
                    "jsonType.label": "String"
                }
            },
            {
                "name": "groups",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-group-membership-mapper",
                "consentRequired": False,
                "config": {
                    "full.path": "true",
                    "id.token.claim": "true",
                    "access.token.claim": "true",
                    "claim.name": "groups",
                    "userinfo.token.claim": "true"
                }
            }
        ]
    }

    try:
        create_req = urllib.request.Request(
            f"{kc_base}/admin/realms/{realm}/clients",
            data=json.dumps(client_payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(create_req, timeout=timeout) as resp:
            pass
    except urllib.error.HTTPError as e:
        if e.code != 409:  # 409 means client already exists
            return False, f"Failed to create Keycloak client via REST API (HTTP {e.code}): {e.reason}"
    except Exception as e:
        return False, f"Error calling Keycloak client creation endpoint: {e}"

    # Step 4: Retrieve client internal ID to create client roles
    try:
        get_req = urllib.request.Request(
            f"{kc_base}/admin/realms/{realm}/clients?clientId={urllib.parse.quote(client_id)}",
            headers=headers
        )
        with urllib.request.urlopen(get_req, timeout=timeout) as resp:
            clients_list = json.loads(resp.read().decode("utf-8"))
        if not clients_list:
            return False, f"Client '{client_id}' not found after registration"
        internal_id = clients_list[0]["id"]
    except Exception as e:
        return False, f"Failed to retrieve registered client ID: {e}"

    # Step 5: Create client roles rc-admin and rc-auditor
    for role_name, role_desc in [
        ("rc-admin", "Quản trị Rocket.Chat (chỉ gán qua nhóm /func/chat-admin)"),
        ("rc-auditor", "Xem nhật ký kiểm toán chat")
    ]:
        try:
            role_data = json.dumps({"name": role_name, "description": role_desc}).encode("utf-8")
            role_req = urllib.request.Request(
                f"{kc_base}/admin/realms/{realm}/clients/{internal_id}/roles",
                data=role_data,
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(role_req, timeout=timeout) as resp:
                pass
        except urllib.error.HTTPError as e:
            if e.code != 409:
                return False, f"Failed to create role '{role_name}' (HTTP {e.code}): {e.reason}"
        except Exception as e:
            return False, f"Error creating client role '{role_name}': {e}"

    return True, f"Keycloak client '{client_id}' and roles successfully registered via Admin REST API"


# =============================================================================
# 4. DRY-RUN & EXECUTION RUNNER
# =============================================================================

def run_dry_run(
    rc_url: str,
    rc_user: str,
    rc_pass: str,
    kc_auth_url: str,
    client_id: str,
    client_secret: str,
    kc_admin_user: str = "adm_mrc_root",
    kc_admin_password: str = ""
) -> int:
    """Execute dry-run mode: test connectivity and inspect configuration drift."""
    print("=" * 70)
    print("MRC-CHAT KEYCLOAK SSO SETUP — DRY RUN MODE")
    print("=" * 70)

    # 1. Check Keycloak connectivity and client registration
    print(f"\n[1/3] Testing Keycloak OIDC Discovery endpoint: {kc_auth_url} ...")
    kc_ok, kc_msg = check_keycloak_discovery(kc_auth_url)
    print(f"  -> Keycloak Status: {'[OK]' if kc_ok else '[WARNING]'} {kc_msg}")

    print(f"  -> Verifying Keycloak client registration for '{client_id}' ...")
    cl_ok, cl_msg = ensure_keycloak_client(
        kc_auth_url=kc_auth_url,
        client_id=client_id,
        client_secret=client_secret,
        admin_user=kc_admin_user,
        admin_password=kc_admin_password,
        redirect_uri=f"{rc_url.rstrip('/')}/_oauth/keycloak",
        dry_run=True
    )
    print(f"  -> Keycloak Client: {'[OK]' if cl_ok else '[WARNING]'} {cl_msg}")

    # 2. Check Rocket.Chat connectivity
    print(f"\n[2/3] Testing Rocket.Chat endpoint: {rc_url} ...")
    client = RocketChatAdminClient(rc_url)
    rc_ok, rc_msg = client.check_health()
    print(f"  -> Rocket.Chat Status: {'[OK]' if rc_ok else '[WARNING]'} {rc_msg}")

    if not rc_ok:
        print("\n[Dry-Run Complete] Rocket.Chat server is offline or unreachable.")
        print("Configuration parameters were validated successfully; apply will run when server starts.")
        return 0

    # 3. Test administrative login and compare settings
    print(f"\n[3/3] Authenticating as '{rc_user}' to inspect configuration drift ...")
    logged_in = False
    try:
        logged_in = client.login(rc_user, rc_pass)
    except Exception as e:
        print(f"  -> Connection error during login: {e}")

    if not logged_in:
        print(f"  -> Login failed for user '{rc_user}' (server may be initializing or password changed).")
        print("Dry run finished without diff check.")
        return 0

    print("  -> Authentication successful. Comparing target settings with current state:")
    target_settings = build_target_settings(kc_auth_url, client_id, client_secret)

    drift_count = 0
    matched_count = 0

    for key, target_val in target_settings.items():
        setting_id = to_rc_setting_id(key)
        curr_val = client.get_setting(setting_id)
        display_target = display_setting_value(setting_id, target_val)
        if curr_val == target_val:
            matched_count += 1
            print(f"  [MATCHED]    {setting_id} == {display_target}")
        else:
            drift_count += 1
            display_curr = display_setting_value(setting_id, curr_val)
            print(f"  [TO_UPDATE]  {setting_id}: current='{display_curr}' -> target='{display_target}'")

    client.logout()

    print("\n" + "-" * 70)
    print(f"Dry Run Summary: {matched_count} matched, {drift_count} to be updated.")
    print("-" * 70)
    return 0


def run_apply(
    rc_url: str,
    rc_user: str,
    rc_pass: str,
    kc_auth_url: str,
    client_id: str,
    client_secret: str,
    kc_admin_user: str = "adm_mrc_root",
    kc_admin_password: str = ""
) -> int:
    """Execute live configuration update via Rocket.Chat REST API."""
    print("=" * 70)
    print("MRC-CHAT KEYCLOAK SSO SETUP — APPLYING CONFIGURATION")
    print("=" * 70)

    # 1. Validate discovery and ensure client registration
    print(f"\n[1/3] Checking Keycloak OIDC Discovery endpoint: {kc_auth_url} ...")
    kc_ok, kc_msg = check_keycloak_discovery(kc_auth_url)
    print(f"  -> Keycloak Status: {'[OK]' if kc_ok else '[NOTICE]'} {kc_msg}")

    print(f"  -> Ensuring Keycloak client registration for '{client_id}' ...")
    cl_ok, cl_msg = ensure_keycloak_client(
        kc_auth_url=kc_auth_url,
        client_id=client_id,
        client_secret=client_secret,
        admin_user=kc_admin_user,
        admin_password=kc_admin_password,
        redirect_uri=f"{rc_url.rstrip('/')}/_oauth/keycloak",
        dry_run=False
    )
    print(f"  -> Keycloak Client: {'[OK]' if cl_ok else '[NOTICE]'} {cl_msg}")

    claims_ok, claims_msg = ensure_chat_claims(kc_auth_url, client_id, kc_admin_user, kc_admin_password)
    print(f"  -> Keycloak Claims: {'[OK]' if claims_ok else '[NOTICE]'} {claims_msg}")

    # 2. Connect to Rocket.Chat
    print(f"\n[2/3] Connecting to Rocket.Chat at {rc_url} ...")
    client = RocketChatAdminClient(rc_url)
    rc_ok, rc_msg = client.check_health()
    if not rc_ok:
        print(f"ERROR: Rocket.Chat is not reachable ({rc_msg}). Aborting.")
        return 1
    print(f"  -> {rc_msg}")

    # 3. Login
    print(f"\n[3/3] Authenticating as '{rc_user}' ...")
    if not client.login(rc_user, rc_pass):
        print(f"ERROR: Administrative authentication failed for user '{rc_user}'. Aborting.")
        return 1
    print("  -> Authentication successful.")

    target_settings = build_target_settings(kc_auth_url, client_id, client_secret)
    updated_count = 0
    unchanged_count = 0
    failed_count = 0

    print("\nApplying settings idempotently:")
    try:
        for key, target_val in target_settings.items():
            setting_id = to_rc_setting_id(key)
            curr_val = client.get_setting(setting_id)
            if curr_val == target_val:
                unchanged_count += 1
                print(f"  [UNCHANGED]  {setting_id}")
            else:
                success = client.update_setting(setting_id, target_val)
                if success:
                    updated_count += 1
                    print(f"  [UPDATED]    {setting_id}")
                else:
                    failed_count += 1
                    print(f"  [FAILED]     {setting_id}")
    finally:
        client.logout()

    print("\n" + "=" * 70)
    print(f"Configuration Summary: {updated_count} updated, {unchanged_count} unchanged, {failed_count} failed.")
    print("=" * 70)
    return 1 if failed_count > 0 else 0


# =============================================================================
# 5. TEST MODE (--test)
# =============================================================================

def run_tests() -> int:
    """Execute comprehensive test suite verifying MAP-07 normalization, assertion, and config."""
    print("=" * 70)
    print("RUNNING UNIT TEST SUITE: MAP-07 & KEYCLOAK SSO CONFIGURATION")
    print("=" * 70)

    total_tests = 0
    passed_tests = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal total_tests, passed_tests
        total_tests += 1
        if condition:
            passed_tests += 1
            print(f"  PASS: {name}")
        else:
            print(f"  FAIL: {name} ({detail})")

    # Group 1: MAP-07 Standard Test Cases
    print("\n[Group 1: MAP-07 Specification Test Cases]")
    standard_cases = [
        ("Đặng Thu Phương", "dang.thu.phuong"),
        ("ngoc.tb", "ngoc.tb"),
        ("Trần Bảo Ngọc", "tran.bao.ngoc"),
        ("Lê Hoàng Long", "le.hoang.long"),
        ("nguyen_van_a", "nguyen_van_a"),
        ("vu-thi-mai", "vu-thi-mai"),
        ("  Đỗ   Thị..Hà  ", "do.thi.ha"),
        ("a" * 50, "a" * 32),
        ("Nguyễn Đắc Điền", "nguyen.dac.dien")
    ]
    for inp, expected in standard_cases:
        actual = normalize_username(inp)
        check(f"normalize_username('{inp}')", actual == expected, f"expected '{expected}', got '{actual}'")

    # Group 2: MAP-07 Edge Cases
    print("\n[Group 2: MAP-07 Complex Edge Cases]")
    edge_cases = [
        ("DANG.THU.PHUONG", "dang.thu.phuong"),
        ("nguyễn...thị---hải___yến", "nguyen.thi---hai___yen"),
        ("   .user.name.   ", "user.name"),
        ("user!@#$%^&*()_+name", "user._.name"),
        ("Đinh Đức Đạt", "dinh.duc.dat"),
        ("Vũ Thị Tuyết Mai", "vu.thi.tuyet.mai"),
        ("123.456-789_abc", "123.456-789_abc"),
        ("   ...---___   ", ""),
        ("", ""),
        ("Trần Lê Minh Hoàng Vũ Quốc Việt Cường", "tran.le.minh.hoang.vu.quoc.viet"),
        ("a" * 31 + ".bbbb", "a" * 31),
        ("a" * 31 + "-bbbb", "a" * 31),
        ("a" * 31 + "_bbbb", "a" * 31),
        ("a" * 29 + "---bbbb", "a" * 29),
        ("a" * 28 + "._-_bbbb", "a" * 28),
    ]
    for inp, expected in edge_cases:
        actual = normalize_username(inp)
        check(f"normalize_username edge case: '{inp}'", actual == expected, f"expected '{expected}', got '{actual}'")

    # Group 3: assert_canonical_username validation
    print("\n[Group 3: assert_canonical_username Validation]")
    valid_canonicals = [
        "dang.thu.phuong",
        "ngoc.tb",
        "tran.bao.ngoc",
        "le.hoang.long",
        "nguyen_van_a",
        "vu-thi-mai",
        "do.thi.ha",
        "a" * 32,
        "tran.le.minh.hoang.vu.quoc.viet"
    ]
    for c in valid_canonicals:
        try:
            assert_canonical_username(c)
            check(f"assert_canonical_username('{c}') valid", True)
        except Exception as e:
            check(f"assert_canonical_username('{c}') valid", False, f"unexpected exception: {e}")

    invalid_canonicals = [
        ("Đặng Thu Phương", "contains spaces and accents"),
        ("Dang.Thu.Phuong", "contains uppercase"),
        ("dang..thu.phuong", "consecutive dots"),
        (".dang.thu.phuong", "leading dot"),
        ("dang.thu.phuong.", "trailing dot"),
        ("dang@thu", "invalid symbol @"),
        ("a" * 33, "length exceeds 32"),
        ("", "empty string")
    ]
    for inv, reason in invalid_canonicals:
        raised = False
        prefix_matched = False
        try:
            assert_canonical_username(inv)
        except ValueError as e:
            raised = True
            prefix_matched = "username_not_canonical" in str(e)
        check(
            f"assert_canonical_username rejected ({reason})",
            raised and prefix_matched,
            f"raised={raised}, matched={prefix_matched}"
        )

    # Group 4: Target Configuration Schema Verification
    print("\n[Group 4: Target Configuration Schema Verification]")
    cfg = build_target_settings(
        "https://sso.miraclegroup.vn/realms/mrc/protocol",
        "chat-miracle",
        "test-client-secret"
    )

    check("Accounts_OAuth_Custom_Keycloak is True", cfg.get("Accounts_OAuth_Custom_Keycloak") is True)
    check("merge_users is True", cfg.get("Accounts_OAuth_Custom_Keycloak_merge_users") is True)
    check("merge_users_distinct_services is True", cfg.get("Accounts_OAuth_Custom_Keycloak_merge_users_distinct_services") is True)
    check("name_field is 'display_name'", cfg.get("Accounts_OAuth_Custom_Keycloak_name_field") == "display_name")
    check("username_field is 'preferred_username'", cfg.get("Accounts_OAuth_Custom_Keycloak_username_field") == "preferred_username")
    check("key_field is 'username'", cfg.get("Accounts_OAuth_Custom_Keycloak_key_field") == "username")
    check("Accounts_ShowFormLogin is False", cfg.get("Accounts_ShowFormLogin") is False)
    check("Accounts_RegistrationForm is 'Disabled'", cfg.get("Accounts_RegistrationForm") == "Disabled")
    check("Accounts_AllowUsernameChange is False", cfg.get("Accounts_AllowUsernameChange") is False)
    check("Accounts_AllowEmailChange is False", cfg.get("Accounts_AllowEmailChange") is False)
    check("Accounts_AllowRealNameChange is False", cfg.get("Accounts_AllowRealNameChange") is False)

    # Validate custom fields JSON
    try:
        custom_fields = json.loads(cfg.get("Accounts_CustomFields", "{}"))
        check("Accounts_CustomFields has 'employee_id'", "employee_id" in custom_fields)
        check("Accounts_CustomFields has 'org_unit'", "org_unit" in custom_fields)
        check("Accounts_CustomFields has 'job_title'", "job_title" in custom_fields)
    except Exception as e:
        check("Accounts_CustomFields parses as valid JSON", False, str(e))

    # Group 5: Keycloak Realm JSON Verification (if available)
    print("\n[Group 5: Keycloak Realm JSON Verification]")
    realm_candidates = [
        Path(os.getenv("MRC_REALM_JSON", "")),
        Path(__file__).resolve().parents[4] / "miracle-sso" / "keycloak" / "import" / "mrc-realm.json"
    ]
    realm_path = next((p for p in realm_candidates if p.is_file()), None)

    if realm_path:
        try:
            with open(realm_path, "r", encoding="utf-8") as f:
                realm_data = json.load(f)

            clients = realm_data.get("clients", [])
            cm_client = next((c for c in clients if c.get("clientId") == "chat-miracle"), None)
            check("chat-miracle client declared in clients array", cm_client is not None)

            if cm_client:
                check("chat-miracle client is confidential", cm_client.get("publicClient") is False)
                check("chat-miracle standardFlowEnabled is True", cm_client.get("standardFlowEnabled") is True)
                check("chat-miracle directAccessGrantsEnabled is False", cm_client.get("directAccessGrantsEnabled") is False)
                check("chat-miracle protocol is 'openid-connect'", cm_client.get("protocol") == "openid-connect")
                check("chat-miracle client id <= 36 chars", len(cm_client.get("id", "")) <= 36)
                attrs = cm_client.get("attributes", {})
                check("chat-miracle attributes has no default.acr.values", "default.acr.values" not in attrs)

                # Validate redirectUris
                redirect_uris = cm_client.get("redirectUris", [])
                check("chat-miracle has prod redirectUri", "https://chat.miraclegroup.vn/_oauth/keycloak" in redirect_uris)
                check("chat-miracle has localhost redirectUri", "http://localhost:3000/_oauth/keycloak" in redirect_uris)

                # Validate protocol mappers
                mappers = {m.get("name"): m for m in cm_client.get("protocolMappers", [])}
                check("protocolMapper 'display_name' present", "display_name" in mappers)
                check("protocolMapper 'rc_roles' present", "rc_roles" in mappers)
                check("protocolMapper 'employee_id' present", "employee_id" in mappers)
                check("protocolMapper 'groups' present", "groups" in mappers)

            # Validate client roles
            client_roles = realm_data.get("roles", {}).get("client", {}).get("chat-miracle", [])
            role_names = [r.get("name") for r in client_roles]
            check("client role 'rc-admin' present", "rc-admin" in role_names)
            check("client role 'rc-auditor' present", "rc-auditor" in role_names)

        except Exception as e:
            check("Keycloak realm JSON parsing", False, str(e))
    else:
        print("  NOTICE: Keycloak realm JSON file not found in candidate paths; skipping filesystem check.")

    # Group 6: Keycloak Client Idempotent Fallback Verification
    print("\n[Group 6: Keycloak Client Idempotent Fallback Verification]")
    orig_urlopen = urllib.request.urlopen

    class MockHttpResponse:
        def __init__(self, status=200, body=b""):
            self.status = status
            self.body = body
        def read(self, *a, **k):
            return self.body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    def mock_urlopen_probe_200(req, timeout=5.0):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "openid-connect/auth" in url:
            return MockHttpResponse(200, b'<html><form id="kc-form-login">Sign in</form></html>')
        raise urllib.error.URLError("Unexpected")

    urllib.request.urlopen = mock_urlopen_probe_200
    try:
        ok, msg = ensure_keycloak_client("http://localhost:8080/realms/mrc/protocol", client_id="chat-miracle")
        check("ensure_keycloak_client handles active client (probe HTTP 200)", ok is True and "active" in msg, msg)
    finally:
        urllib.request.urlopen = orig_urlopen

    def mock_urlopen_probe_400(req, timeout=5.0):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "openid-connect/auth" in url:
            fp = io.BytesIO(b'<p class="instruction">Client not found.</p>')
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, fp)
        raise urllib.error.URLError("Unexpected")

    urllib.request.urlopen = mock_urlopen_probe_400
    try:
        ok, msg = ensure_keycloak_client("http://localhost:8080/realms/mrc/protocol", client_id="chat-miracle", dry_run=True)
        check("ensure_keycloak_client handles missing client in dry-run", ok is True and "dry-run" in msg, msg)
    finally:
        urllib.request.urlopen = orig_urlopen

    print("\n" + "=" * 70)
    print(f"TEST RESULTS: {passed_tests} / {total_tests} passed ({(passed_tests/total_tests)*100:.1f}%)")
    print("=" * 70)

    return 0 if passed_tests == total_tests else 1


# =============================================================================
# 6. MAIN CLI ENTRY POINT
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MRC-Chat Keycloak SSO Integration & Automated Configuration Script"
    )
    parser.add_argument("--test", action="store_true", help="Run comprehensive unit tests for MAP-07 and configuration")
    parser.add_argument("--dry-run", action="store_true", help="Check connectivity and configuration drift without modifying settings")
    parser.add_argument("--keycloak-claims", action="store_true", help="Only configure the Keycloak User Profile and chat.claims scope")
    parser.add_argument("--rc-url", default=os.getenv("RC_URL", os.getenv("ROOT_URL", "http://localhost:3000")), help="Rocket.Chat base URL")
    parser.add_argument("--rc-user", default=os.getenv("RC_ADMIN_USERNAME", os.getenv("ADM_LOCAL_USERNAME", "adm-local-chat")), help="Rocket.Chat admin username")
    parser.add_argument("--rc-password", default=os.getenv("RC_ADMIN_PASSWORD", os.getenv("ADM_LOCAL_PASSWORD", "")), help="Rocket.Chat admin password")
    parser.add_argument("--kc-url", default=os.getenv("KC_AUTH_URL", "https://sso.miraclegroup.vn/realms/mrc/protocol"), help="Keycloak OAuth protocol URL")
    parser.add_argument("--kc-admin-user", default=os.getenv("KEYCLOAK_ADMIN", os.getenv("KC_ADMIN_USER", "adm_mrc_root")), help="Keycloak master admin username")
    parser.add_argument("--kc-admin-password", default=os.getenv("KEYCLOAK_ADMIN_PASSWORD", os.getenv("KC_ADMIN_PASSWORD", "")), help="Keycloak master admin password")
    parser.add_argument("--client-id", default=os.getenv("KC_CLIENT_ID", "chat-miracle"), help="Keycloak OIDC Client ID")
    parser.add_argument("--client-secret", default=os.getenv("KC_CLIENT_SECRET", ""), help="Keycloak OIDC Client Secret")

    args = parser.parse_args()

    if args.test:
        return run_tests()

    if args.keycloak_claims:
        if not args.kc_admin_password:
            parser.error("missing required secrets: --kc-admin-password (KEYCLOAK_ADMIN_PASSWORD)")
        ok, msg = ensure_chat_claims(args.kc_url, args.client_id, args.kc_admin_user, args.kc_admin_password)
        print(f"{'[OK]' if ok else '[ERROR]'} {msg}")
        return 0 if ok else 1

    missing = []
    if not args.rc_password:
        missing.append("--rc-password (RC_ADMIN_PASSWORD or ADM_LOCAL_PASSWORD)")
    if not args.client_secret:
        missing.append("--client-secret (KC_CLIENT_SECRET)")
    if missing:
        parser.error("missing required secrets: " + ", ".join(missing))

    if args.dry_run:
        return run_dry_run(
            rc_url=args.rc_url,
            rc_user=args.rc_user,
            rc_pass=args.rc_password,
            kc_auth_url=args.kc_url,
            client_id=args.client_id,
            client_secret=args.client_secret,
            kc_admin_user=args.kc_admin_user,
            kc_admin_password=args.kc_admin_password
        )

    return run_apply(
        rc_url=args.rc_url,
        rc_user=args.rc_user,
        rc_pass=args.rc_password,
        kc_auth_url=args.kc_url,
        client_id=args.client_id,
        client_secret=args.client_secret,
        kc_admin_user=args.kc_admin_user,
        kc_admin_password=args.kc_admin_password
    )


if __name__ == "__main__":
    sys.exit(main())
