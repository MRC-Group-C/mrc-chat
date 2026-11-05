#!/usr/bin/env python3
"""
Structural & Configuration Verification Tests for Milestone 2 SSO Integration.
Location: mrc-chat/mrc/tests/test_configuration.py

Validates:
1. Keycloak realm configuration (mrc-realm.json):
   - Valid JSON syntax
   - Client 'chat-miracle' declared with confidential settings & redirect URIs
   - Protocol mappers: display_name, rc_roles, employee_id, groups
   - Client roles: rc-admin, rc-auditor
   - User attributes mapped via protocol mappers & absence of invalid root fields
2. Target settings dictionary in setup_sso.py:
   - Custom OAuth Keycloak parameters
   - Security lockdown parameters
   - Custom fields JSON schema
3. Health check script routines and schema
4. Clean Upstream Invariant:
   - 0 modified/added files in mrc-chat/apps/, mrc-chat/packages/, mrc-chat/package.json
   - Zero i18n / localization files created
"""

import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
import pytest

MRC_CHAT_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_DIR = MRC_CHAT_DIR.parent
REALM_JSON_PATH = PROJECT_DIR / "miracle-sso" / "keycloak" / "import" / "mrc-realm.json"
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "integration" / "scripts"

for p in [str(MRC_CHAT_DIR), str(SCRIPTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from mrc.integration.scripts.setup_sso import build_target_settings
    from mrc.integration.scripts.healthcheck import check_mongodb, check_rocketchat, check_keycloak
except ImportError:
    from setup_sso import build_target_settings
    from healthcheck import check_mongodb, check_rocketchat, check_keycloak


class TestKeycloakRealmConfiguration:
    """Validate Keycloak realm JSON structure and chat-miracle client."""

    @pytest.fixture(scope="class")
    def realm_data(self):
        assert REALM_JSON_PATH.is_file(), f"Missing realm file at {REALM_JSON_PATH}"
        content = REALM_JSON_PATH.read_text(encoding="utf-8")
        return json.loads(content)

    def test_realm_json_syntax_validity(self, realm_data):
        """Ensure realm JSON parses cleanly and has expected realm name."""
        assert realm_data.get("realm") == "mrc"
        assert realm_data.get("enabled") is True

    def test_chat_miracle_client_declaration(self, realm_data):
        """Verify chat-miracle client exists and conforms to confidential client spec."""
        clients = realm_data.get("clients", [])
        client = next((c for c in clients if c.get("clientId") == "chat-miracle"), None)
        assert client is not None, "chat-miracle client not found in realm clients array"

        assert client.get("protocol") == "openid-connect"
        assert client.get("clientAuthenticatorType") == "client-secret"
        assert client.get("secret") == "miracle-chat-oidc-secret-2026-prod"
        assert client.get("publicClient") is False
        assert client.get("standardFlowEnabled") is True
        assert client.get("directAccessGrantsEnabled") is False

        # Redirect URIs
        redirect_uris = client.get("redirectUris", [])
        assert "https://chat.miraclegroup.vn/_oauth/keycloak" in redirect_uris
        assert "http://localhost:3000/_oauth/keycloak" in redirect_uris
        assert "http://127.0.0.1:3000/_oauth/keycloak" in redirect_uris

        # Web Origins
        web_origins = client.get("webOrigins", [])
        assert "+" in web_origins
        assert "https://chat.miraclegroup.vn" in web_origins
        assert "http://localhost:3000" in web_origins

    def test_chat_miracle_protocol_mappers(self, realm_data):
        """Verify all 4 protocol mappers (display_name, rc_roles, employee_id, groups)."""
        clients = realm_data.get("clients", [])
        client = next((c for c in clients if c.get("clientId") == "chat-miracle"), {})
        mappers = {m.get("name"): m for m in client.get("protocolMappers", [])}

        # 1. display_name mapper
        assert "display_name" in mappers, "display_name mapper missing"
        dn_mapper = mappers["display_name"]
        assert dn_mapper.get("protocolMapper") == "oidc-usermodel-attribute-mapper"
        assert dn_mapper.get("config", {}).get("user.attribute") == "full_name"
        assert dn_mapper.get("config", {}).get("claim.name") == "display_name"
        assert dn_mapper.get("config", {}).get("userinfo.token.claim") == "true"
        assert dn_mapper.get("config", {}).get("id.token.claim") == "true"

        # 2. rc_roles mapper
        assert "rc_roles" in mappers, "rc_roles mapper missing"
        rc_mapper = mappers["rc_roles"]
        assert rc_mapper.get("protocolMapper") == "oidc-usermodel-client-role-mapper"
        assert rc_mapper.get("config", {}).get("usermodel.clientRoleMapping.clientId") == "chat-miracle"
        assert rc_mapper.get("config", {}).get("claim.name") == "rc_roles"
        assert rc_mapper.get("config", {}).get("multivalued") == "true"

        # 3. employee_id mapper
        assert "employee_id" in mappers, "employee_id mapper missing"
        emp_mapper = mappers["employee_id"]
        assert emp_mapper.get("protocolMapper") == "oidc-usermodel-attribute-mapper"
        assert emp_mapper.get("config", {}).get("user.attribute") == "employee_id"
        assert emp_mapper.get("config", {}).get("claim.name") == "employee_id"

        # 4. groups mapper
        assert "groups" in mappers, "groups mapper missing"
        grp_mapper = mappers["groups"]
        assert grp_mapper.get("protocolMapper") == "oidc-group-membership-mapper"
        assert grp_mapper.get("config", {}).get("claim.name") == "groups"

    def test_client_roles_declaration(self, realm_data):
        """Verify client roles rc-admin and rc-auditor exist."""
        client_roles = realm_data.get("roles", {}).get("client", {}).get("chat-miracle", [])
        role_names = [r.get("name") for r in client_roles]
        assert "rc-admin" in role_names, "rc-admin role missing in client roles"
        assert "rc-auditor" in role_names, "rc-auditor role missing in client roles"

    def test_user_profile_configuration(self, realm_data):
        """Verify user attributes are mapped via protocol mappers and no invalid root field exists.

        In Keycloak 26 (Quarkus), 'userProfileConfiguration' is NOT a valid root field on
        RealmRepresentation and causes fatal UnrecognizedPropertyException on import.
        User attributes (full_name -> display_name, employee_id -> employee_id) are
        authentically handled via protocol mappers on the chat-miracle client.
        """
        assert "userProfileConfiguration" not in realm_data, (
            "Invalid root field 'userProfileConfiguration' found in mrc-realm.json. "
            "Keycloak 26 RealmRepresentation rejects this field on startup."
        )
        # Verify protocol mappers correctly map user attributes
        clients = realm_data.get("clients", [])
        client = next((c for c in clients if c.get("clientId") == "chat-miracle"), {})
        mappers = {m.get("name"): m for m in client.get("protocolMappers", [])}
        assert mappers.get("display_name", {}).get("config", {}).get("user.attribute") == "full_name"
        assert mappers.get("employee_id", {}).get("config", {}).get("user.attribute") == "employee_id"

    def test_client_id_uuid_length_and_acr_constraints(self, realm_data):
        """Verify client IDs and container IDs do not exceed PostgreSQL VARCHAR(36) limit, and ACR attributes are valid."""
        clients = realm_data.get("clients", [])
        for c in clients:
            cid = c.get("id")
            if cid:
                assert len(cid) <= 36, f"Client id '{cid}' exceeds 36 chars (Postgres VARCHAR(36) constraint)"
            attrs = c.get("attributes", {})
            assert "default.acr.values" not in attrs, (
                "default.acr.values causes fatal Keycloak 26 ACR validation error"
            )
            assert "min_acr" not in attrs, "min_acr not permitted without ACR mapping"

        for client_name, roles in realm_data.get("roles", {}).get("client", {}).items():
            for r in roles:
                rid = r.get("id")
                if rid:
                    assert len(rid) <= 36, f"Role id '{rid}' exceeds 36 chars"
                container_id = r.get("containerId")
                if container_id:
                    assert len(container_id) <= 36, f"Role containerId '{container_id}' exceeds 36 chars"


class TestSetupSsoTargetSettings:
    """Validate target configuration schema produced by setup_sso.py."""

    @pytest.fixture(scope="class")
    def settings(self):
        return build_target_settings(
            kc_auth_url="https://sso.miraclegroup.vn/realms/mrc/protocol",
            client_id="chat-miracle",
            client_secret="miracle-chat-oidc-secret-2026-prod"
        )

    def test_custom_oauth_core_parameters(self, settings):
        """Verify critical Custom OAuth settings."""
        assert settings.get("Accounts_OAuth_Custom_Keycloak") is True
        assert settings.get("Accounts_OAuth_Custom_Keycloak_url") == "https://sso.miraclegroup.vn/realms/mrc/protocol"
        assert settings.get("Accounts_OAuth_Custom_Keycloak_id") == "chat-miracle"
        assert settings.get("Accounts_OAuth_Custom_Keycloak_secret") == "miracle-chat-oidc-secret-2026-prod"
        assert settings.get("Accounts_OAuth_Custom_Keycloak_name_field") == "display_name"
        assert settings.get("Accounts_OAuth_Custom_Keycloak_username_field") == "preferred_username"
        assert settings.get("Accounts_OAuth_Custom_Keycloak_key_field") == "username"

    def test_merge_users_flags_enabled(self, settings):
        """Verify both merge_users and merge_users_distinct_services are strictly True."""
        assert settings.get("Accounts_OAuth_Custom_Keycloak_merge_users") is True
        assert settings.get("Accounts_OAuth_Custom_Keycloak_merge_users_distinct_services") is True

    def test_security_lockdown_settings(self, settings):
        """Verify single source of truth security lockdown settings."""
        assert settings.get("Accounts_ShowFormLogin") is False
        assert settings.get("Accounts_RegistrationForm") == "Disabled"
        assert settings.get("Accounts_AllowUsernameChange") is False
        assert settings.get("Accounts_AllowEmailChange") is False
        assert settings.get("Accounts_AllowRealNameChange") is False
        assert settings.get("Accounts_AllowPasswordChange") is False
        assert settings.get("Accounts_AllowPasswordChangeForOAuthUsers") is False
        assert settings.get("Accounts_AllowDeleteOwnAccount") is False
        assert settings.get("Accounts_TwoFactorAuthentication_Enabled") is False

    def test_custom_fields_json_structure(self, settings):
        """Verify Accounts_CustomFields JSON contains employee_id, org_unit, job_title."""
        cf_raw = settings.get("Accounts_CustomFields")
        assert isinstance(cf_raw, str)
        cf = json.loads(cf_raw)
        assert "employee_id" in cf
        assert "org_unit" in cf
        assert "job_title" in cf


class TestHealthcheckRoutines:
    """Validate healthcheck module behavior."""

    def test_healthcheck_schemas_and_return_structures(self):
        """Verify health check functions return structured dictionaries."""
        m_res = check_mongodb(host="127.0.0.1", port=65534, timeout=0.1)
        assert "component" in m_res and m_res["component"] == "mongodb"
        assert m_res["healthy"] is False

        r_res = check_rocketchat(base_url="http://127.0.0.1:65534", timeout=0.1)
        assert "component" in r_res and r_res["component"] == "rocketchat"
        assert r_res["healthy"] is False

        k_res = check_keycloak(kc_url="http://127.0.0.1:65534/realms/mrc/protocol", timeout=0.1)
        assert "component" in k_res and k_res["component"] == "keycloak"
        assert k_res["healthy"] is False

    def test_healthcheck_keycloak_detects_missing_client(self, monkeypatch):
        """Verify check_keycloak marks component UNHEALTHY when auth probe returns HTTP 400 Client not found."""
        from unittest.mock import MagicMock
        discovery_payload = json.dumps({
            "issuer": "http://localhost:8080/realms/mrc",
            "authorization_endpoint": "http://localhost:8080/realms/mrc/protocol/openid-connect/auth",
            "token_endpoint": "http://localhost:8080/realms/mrc/protocol/openid-connect/token"
        }).encode("utf-8")

        def mock_urlopen(req, timeout=4.0):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if ".well-known/openid-configuration" in url:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.read.return_value = discovery_payload
                mock_resp.__enter__.return_value = mock_resp
                return mock_resp
            elif "openid-connect/auth" in url:
                fp = io.BytesIO(b'<div id="kc-error-message"><p class="instruction">Client not found.</p></div>')
                raise urllib.error.HTTPError(url, 400, "Bad Request", {}, fp)
            raise urllib.error.URLError("Unexpected URL")

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

        res = check_keycloak(kc_url="http://localhost:8080/realms/mrc/protocol", client_id="chat-miracle")
        assert res["healthy"] is False
        assert res["status"] == "UNHEALTHY"
        assert "Client not found" in res["details"].get("error", "")

    def test_healthcheck_keycloak_verifies_valid_client(self, monkeypatch):
        """Verify check_keycloak marks component HEALTHY when auth probe returns HTTP 200 login page."""
        from unittest.mock import MagicMock
        discovery_payload = json.dumps({
            "issuer": "http://localhost:8080/realms/mrc",
            "authorization_endpoint": "http://localhost:8080/realms/mrc/protocol/openid-connect/auth"
        }).encode("utf-8")

        def mock_urlopen(req, timeout=4.0):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if ".well-known/openid-configuration" in url:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.read.return_value = discovery_payload
                mock_resp.__enter__.return_value = mock_resp
                return mock_resp
            elif "openid-connect/auth" in url:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.read.return_value = b'<html><form id="kc-form-login">Sign in to your account</form></html>'
                mock_resp.__enter__.return_value = mock_resp
                return mock_resp
            raise urllib.error.URLError("Unexpected URL")

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

        res = check_keycloak(kc_url="http://localhost:8080/realms/mrc/protocol", client_id="chat-miracle")
        assert res["healthy"] is True
        assert res["status"] == "HEALTHY"
        assert res["client_verified"] is True


class TestLiveKeycloakOidcIntegration:
    """Validate live Keycloak OIDC authorization flow against running container."""

    @pytest.fixture(scope="class")
    def live_kc_auth_url(self):
        url = os.getenv("KC_AUTH_URL", "http://localhost:8080/realms/mrc/protocol")
        normalized_url = url.rstrip("/")
        if normalized_url.endswith("/protocol"):
            base_realm = normalized_url[:-9]
        else:
            base_realm = normalized_url
        discovery_url = f"{base_realm}/.well-known/openid-configuration"
        try:
            req = urllib.request.Request(discovery_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    return url
        except Exception:
            pytest.skip(f"Live Keycloak instance not reachable at {url} (live test skipped)")
        return url

    def test_live_chat_miracle_client_authorization(self, live_kc_auth_url):
        """Verify chat-miracle client is actively registered in running Keycloak and auth endpoint returns HTTP 200."""
        normalized_url = live_kc_auth_url.rstrip("/")
        if normalized_url.endswith("/protocol"):
            auth_endpoint = f"{normalized_url}/openid-connect/auth"
        else:
            auth_endpoint = f"{normalized_url}/protocol/openid-connect/auth"

        probe_url = (
            f"{auth_endpoint}?"
            f"client_id=chat-miracle&response_type=code&"
            f"redirect_uri=http%3A%2F%2Flocalhost%3A3000%2F_oauth%2Fkeycloak"
        )

        try:
            req = urllib.request.Request(probe_url, headers={"Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                assert resp.status == 200, f"Expected HTTP 200 from Keycloak auth endpoint, got {resp.status}"
                body = resp.read().decode("utf-8", errors="replace")
                assert "Client not found" not in body, "Keycloak returned 'Client not found' for chat-miracle"
                assert "Invalid parameter: redirect_uri" not in body, "Keycloak rejected redirect_uri"
                assert any(marker in body.lower() for marker in ["kc-form", "login", "sign in", "đăng nhập", "mrc"]), (
                    "Response does not contain expected Keycloak login form"
                )
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            pytest.fail(f"Live Keycloak OIDC authorization failed with HTTP {e.code}: {err_body[:300]}")


class TestCleanUpstreamInvariant:
    """Verify clean upstream invariant across git repository."""

    def test_no_changes_in_upstream_rocketchat(self):
        """Verify apps/, packages/, package.json are untouched."""
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain", "apps/", "packages/", "package.json"],
                cwd=MRC_CHAT_DIR,
                capture_output=True,
                text=True,
                timeout=20
            )
            if res.returncode == 0:
                assert res.stdout.strip() == "", f"Upstream modifications detected:\n{res.stdout}"
        except FileNotFoundError:
            # git command unavailable
            pass

    def test_clean_upstream_i18n_invariant(self):
        """Verify upstream packages/i18n has zero modifications (Clean Upstream)."""
        res = subprocess.run(
            ["git", "status", "--porcelain", "packages/i18n", "apps"],
            cwd=str(MRC_CHAT_DIR),
            capture_output=True,
            text=True
        )
        if res.returncode == 0:
            assert res.stdout.strip() == "", f"Upstream packages/i18n modifications detected:\n{res.stdout}"
