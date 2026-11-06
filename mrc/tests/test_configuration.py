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
REALM_JSON_PATH = Path(
    os.getenv("MRC_REALM_JSON", PROJECT_DIR / "miracle-sso" / "keycloak" / "import" / "mrc-realm.json")
)
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "integration" / "scripts"

for p in [str(MRC_CHAT_DIR), str(SCRIPTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from mrc.integration.scripts.setup_sso import (
        DEFAULT_CUSTOM_FIELDS,
        build_target_settings,
        display_setting_value,
        ensure_account_console_mfa,
        ensure_chat_claims,
        ensure_keycloak_client,
        main as setup_sso_main,
        to_rc_setting_id,
    )
    from mrc.integration.scripts.healthcheck import check_mongodb, check_rocketchat, check_keycloak
except ImportError:
    from setup_sso import (
        DEFAULT_CUSTOM_FIELDS,
        build_target_settings,
        display_setting_value,
        ensure_account_console_mfa,
        ensure_chat_claims,
        ensure_keycloak_client,
        main as setup_sso_main,
        to_rc_setting_id,
    )
    from healthcheck import check_mongodb, check_rocketchat, check_keycloak


class TestKeycloakRealmConfiguration:
    """Validate Keycloak realm JSON structure and chat-miracle client."""

    @pytest.fixture(scope="class")
    def realm_data(self):
        if not REALM_JSON_PATH.is_file():
            pytest.skip(f"Realm file not available at {REALM_JSON_PATH} (miracle-sso is not part of this repo)")
        content = REALM_JSON_PATH.read_text(encoding="utf-8")
        return json.loads(content)

    def test_realm_json_syntax_validity(self, realm_data):
        """Ensure realm JSON parses cleanly and has expected realm name."""
        assert realm_data.get("realm") == "mrc"
        assert realm_data.get("enabled") is True

    def test_lockout_events_theme_and_link_lifespans(self, realm_data):
        """Parameters from the SSO SRS appendix that the realm file must declare."""
        assert realm_data["failureFactor"] == 5
        assert realm_data["maxFailureWaitSeconds"] == 900
        assert realm_data["maxDeltaTimeSeconds"] == 43200
        assert realm_data["maxTemporaryLockouts"] == 3
        assert realm_data["permanentLockout"] is False

        assert realm_data["eventsEnabled"] is True
        assert realm_data["eventsExpiration"] == 30 * 24 * 3600
        assert realm_data["adminEventsEnabled"] is True

        assert realm_data["loginTheme"] == "mrc-theme"
        assert realm_data["actionTokenGeneratedByAdminLifespan"] == 72 * 3600
        assert realm_data["attributes"]["actionTokenGeneratedByUserLifespan.reset-credentials"] == "900"

    def test_identity_provider_with_sample_credentials_is_not_enabled(self, realm_data):
        for provider in realm_data.get("identityProviders", []):
            client_id = provider.get("config", {}).get("clientId", "")
            if "sample" in client_id:
                assert provider["enabled"] is False, f"IdP '{provider['alias']}' is enabled with sample credentials"

    def test_mfa_flow_is_bound_realm_wide_with_two_levels(self, realm_data):
        """Level 1 = password, level 2 = OTP; bound on the realm because a client-only override breaks SSO reuse."""
        assert realm_data["browserFlow"] == "browser-mrc"
        assert json.loads(realm_data["attributes"]["acr.loa.map"]) == {"mrc-mfa": "2"}

        flows = {f["alias"]: f for f in realm_data["authenticationFlows"]}
        configs = {c["alias"]: c["config"] for c in realm_data["authenticatorConfig"]}
        levels = {}
        for execution in flows["browser-mrc auth"]["authenticationExecutions"]:
            sub = flows.get(execution.get("flowAlias"))
            if not sub or execution["requirement"] != "CONDITIONAL":
                continue
            steps = {e.get("authenticator"): e for e in sub["authenticationExecutions"]}
            condition = steps["conditional-level-of-authentication"]
            level = configs[condition["authenticatorConfig"]]["loa-condition-level"]
            levels[level] = next(
                name for name in ("auth-username-password-form", "auth-otp-form") if name in steps
            )
            assert condition["requirement"] == "REQUIRED"
            assert steps[levels[level]]["requirement"] == "REQUIRED"
        assert levels == {"1": "auth-username-password-form", "2": "auth-otp-form"}

    def test_otp_policy_matches_the_srs(self, realm_data):
        assert realm_data["otpPolicyType"] == "totp"
        assert realm_data["otpPolicyDigits"] == 6
        assert realm_data["otpPolicyPeriod"] == 30

    def test_chat_client_asks_for_mfa_by_default(self, realm_data):
        client = next(c for c in realm_data["clients"] if c["clientId"] == "chat-miracle")
        assert client["attributes"]["default.acr.values"] == "mrc-mfa"
        assert client.get("authenticationFlowBindingOverrides", {}) == {}

    def test_theme_keeps_parent_layout(self):
        theme = REALM_JSON_PATH.parent.parent / "themes" / "mrc-theme" / "login" / "theme.properties"
        if not theme.is_file():
            pytest.skip("mrc-theme is not part of this repo")
        props = dict(
            line.split("=", 1) for line in theme.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.startswith("#")
        )
        assert props["parent"] == "keycloak"
        assert "css/login.css" in props["styles"].split(), "styles replaces the parent list; login.css keeps the layout"

    def test_chat_miracle_client_declaration(self, realm_data):
        """Verify chat-miracle client exists and conforms to confidential client spec."""
        clients = realm_data.get("clients", [])
        client = next((c for c in clients if c.get("clientId") == "chat-miracle"), None)
        assert client is not None, "chat-miracle client not found in realm clients array"

        assert client.get("protocol") == "openid-connect"
        assert client.get("clientAuthenticatorType") == "client-secret"
        assert client.get("secret"), "chat-miracle must declare a client secret"
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
            assert "min_acr" not in attrs, "Keycloak 26.0 has no minimum ACR; use default.acr.values"
            if "default.acr.values" in attrs:
                assert attrs["default.acr.values"] in json.loads(
                    realm_data["attributes"]["acr.loa.map"]
                ), "default ACR must be mapped in acr.loa.map or Keycloak rejects the login"

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
            client_secret="test-client-secret"
        )

    def test_custom_oauth_core_parameters(self, settings):
        """Verify critical Custom OAuth settings."""
        assert settings.get("Accounts_OAuth_Custom_Keycloak") is True
        assert settings.get("Accounts_OAuth_Custom_Keycloak_url") == "https://sso.miraclegroup.vn/realms/mrc/protocol"
        assert settings.get("Accounts_OAuth_Custom_Keycloak_id") == "chat-miracle"
        assert settings.get("Accounts_OAuth_Custom_Keycloak_secret") == "test-client-secret"
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


class TestSecretsAreRequired:
    """Secrets must come from the environment; there is no built-in fallback."""

    SECRET_ENV_VARS = [
        "RC_ADMIN_PASSWORD", "ADM_LOCAL_PASSWORD", "KEYCLOAK_ADMIN_PASSWORD",
        "KC_ADMIN_PASSWORD", "KC_CLIENT_SECRET",
    ]

    @pytest.mark.parametrize("mode", [[], ["--dry-run"]])
    def test_setup_sso_refuses_to_run_without_secrets(self, monkeypatch, capsys, mode):
        for name in self.SECRET_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(sys, "argv", ["setup_sso.py", *mode])

        with pytest.raises(SystemExit) as exc:
            setup_sso_main()

        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "--rc-password" in err and "--client-secret" in err

    def test_client_registration_refused_without_secrets(self, monkeypatch):
        def client_not_found(req, timeout=5.0):
            url = req.full_url if hasattr(req, "full_url") else req
            assert "/openid-connect/token" not in url, "must not attempt admin login without a password"
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(b"Client not found"))

        monkeypatch.setattr(urllib.request, "urlopen", client_not_found)

        ok, msg = ensure_keycloak_client("http://localhost:8080/realms/mrc/protocol")

        assert ok is False
        assert "KC_CLIENT_SECRET" in msg and "KEYCLOAK_ADMIN_PASSWORD" in msg


class FakeKeycloakAdmin:
    """In-memory stand-in for the Keycloak Admin REST endpoints used by ensure_chat_claims."""

    def __init__(self):
        self.profile = {"attributes": [{"name": "username"}, {"name": "email"}]}
        self.scopes = [{"id": "s-profile", "name": "profile"}]
        self.scope_mappers = []
        self.client_mappers = [
            {"id": "m1", "name": "display_name"},
            {"id": "m2", "name": "rc_roles"},
            {"id": "m3", "name": "employee_id"},
        ]
        self.default_scopes = set()
        self.calls = []

    def urlopen(self, req, timeout=5.0):
        method, url = req.get_method(), req.full_url
        if url.endswith("/token"):
            return io.BytesIO(json.dumps({"access_token": "admin-token"}).encode("utf-8"))
        self.calls.append((method, url))
        result = self._route(method, url, json.loads(req.data) if req.data else None)
        return io.BytesIO(json.dumps(result).encode("utf-8") if method == "GET" else b"")

    def _route(self, method, url, body):
        if url.endswith("/users/profile"):
            if method == "PUT":
                self.profile = body
            return self.profile
        if url.endswith("/client-scopes"):
            if method == "POST":
                self.scopes.append({"id": "s-chat", **body})
                return {}
            return self.scopes
        if "/client-scopes/" in url and url.endswith("/protocol-mappers/models"):
            if method == "POST":
                self.scope_mappers.append(body)
                return {}
            return self.scope_mappers
        if "/clients?clientId=" in url:
            return [{"id": "c-1"}]
        if "/default-client-scopes/" in url:
            self.default_scopes.add(url.rsplit("/", 1)[1])
            return {}
        if url.endswith("/clients/c-1/protocol-mappers/models"):
            return self.client_mappers
        if "/clients/c-1/protocol-mappers/models/" in url and method == "DELETE":
            mapper_id = url.rsplit("/", 1)[1]
            self.client_mappers = [m for m in self.client_mappers if m["id"] != mapper_id]
            return {}
        raise AssertionError(f"Unexpected Keycloak call: {method} {url}")


class TestChatClaims:
    """display_name must reach userinfo from a managed attribute, via the chat.claims scope."""

    KC_URL = "http://localhost:8080/realms/mrc/protocol"

    @pytest.fixture
    def keycloak(self, monkeypatch):
        fake = FakeKeycloakAdmin()
        monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
        return fake

    def test_configures_profile_scope_and_moves_mappers(self, keycloak):
        ok, msg = ensure_chat_claims(self.KC_URL, admin_password="pw")

        assert ok is True, msg
        names = {a["name"] for a in keycloak.profile["attributes"]}
        assert {"full_name", "employee_id"} <= names
        full_name = next(a for a in keycloak.profile["attributes"] if a["name"] == "full_name")
        assert full_name["permissions"]["edit"] == ["admin"], "users must not edit their own full_name"

        display_name = next(m for m in keycloak.scope_mappers if m["name"] == "display_name")
        assert display_name["config"]["user.attribute"] == "full_name"
        assert display_name["config"]["userinfo.token.claim"] == "true"
        assert {m["name"] for m in keycloak.scope_mappers} == {"display_name", "rc_roles"}

        assert keycloak.default_scopes == {"s-chat"}
        assert [m["name"] for m in keycloak.client_mappers] == ["employee_id"]

    def test_scope_attached_before_client_mappers_removed(self, keycloak):
        ensure_chat_claims(self.KC_URL, admin_password="pw")

        attach = next(i for i, (_, u) in enumerate(keycloak.calls) if "/default-client-scopes/" in u)
        first_delete = next(i for i, (m, _) in enumerate(keycloak.calls) if m == "DELETE")
        assert attach < first_delete, "the claim must never disappear during the switch"

    def test_second_run_changes_nothing(self, keycloak):
        ensure_chat_claims(self.KC_URL, admin_password="pw")
        keycloak.calls.clear()

        ok, _ = ensure_chat_claims(self.KC_URL, admin_password="pw")

        assert ok is True
        changes = [(m, u) for m, u in keycloak.calls if m in ("POST", "DELETE") or (m == "PUT" and u.endswith("/users/profile"))]
        assert changes == []

    def test_requires_admin_password(self, keycloak):
        ok, msg = ensure_chat_claims(self.KC_URL)

        assert ok is False
        assert "KEYCLOAK_ADMIN_PASSWORD" in msg
        assert keycloak.calls == []


class TestAccountConsoleMfa:
    """Account console can reset credentials, so it must demand the second factor too."""

    KC_URL = "http://localhost:8080/realms/mrc/protocol"

    @pytest.fixture
    def keycloak(self, monkeypatch):
        state = {"client": {"id": "ac-1", "clientId": "account-console", "attributes": {}}, "puts": 0}

        def urlopen(req, timeout=5.0):
            url, method = req.full_url, req.get_method()
            if url.endswith("/token"):
                return io.BytesIO(json.dumps({"access_token": "t"}).encode("utf-8"))
            if "/clients?clientId=" in url:
                return io.BytesIO(json.dumps([state["client"]]).encode("utf-8"))
            if url.endswith("/clients/ac-1") and method == "PUT":
                state["client"] = json.loads(req.data)
                state["puts"] += 1
                return io.BytesIO(b"")
            raise AssertionError(f"Unexpected Keycloak call: {method} {url}")

        monkeypatch.setattr(urllib.request, "urlopen", urlopen)
        return state

    def test_sets_default_acr_and_keeps_other_attributes(self, keycloak):
        keycloak["client"]["attributes"] = {"pkce.code.challenge.method": "S256"}

        ok, msg = ensure_account_console_mfa(self.KC_URL, admin_password="pw")

        assert ok is True, msg
        assert keycloak["client"]["attributes"] == {"pkce.code.challenge.method": "S256", "default.acr.values": "mrc-mfa"}

    def test_second_run_changes_nothing(self, keycloak):
        ensure_account_console_mfa(self.KC_URL, admin_password="pw")
        ensure_account_console_mfa(self.KC_URL, admin_password="pw")

        assert keycloak["puts"] == 1

    def test_requires_admin_password(self, keycloak):
        ok, msg = ensure_account_console_mfa(self.KC_URL)

        assert ok is False
        assert "KEYCLOAK_ADMIN_PASSWORD" in msg


class TestSettingHelpers:
    """Rocket.Chat stores OAuth settings under hyphenated IDs."""

    @pytest.mark.parametrize("key, expected", [
        ("Accounts_OAuth_Custom_Keycloak", "Accounts_OAuth_Custom-Keycloak"),
        ("Accounts_OAuth_Custom_Keycloak_token_path", "Accounts_OAuth_Custom-Keycloak-token_path"),
        ("Accounts_ShowFormLogin", "Accounts_ShowFormLogin"),
    ])
    def test_to_rc_setting_id(self, key, expected):
        assert to_rc_setting_id(key) == expected

    def test_secret_values_are_never_displayed(self):
        assert display_setting_value("Accounts_OAuth_Custom-Keycloak-secret", "real-value") == "***"
        assert display_setting_value("Site_Name", "Chat Miracle") == "Chat Miracle"


class TestComposeCustomFields:
    """Custom fields are configuration as code and must match the provisioning schema."""

    def test_compose_declares_same_custom_fields(self):
        compose = (MRC_CHAT_DIR / "mrc" / "deploy" / "compose.yaml").read_text(encoding="utf-8")
        line = next(l for l in compose.splitlines() if "OVERWRITE_SETTING_Accounts_CustomFields=" in l)

        declared = json.loads(line.split("=", 1)[1].strip().strip("'"))

        assert declared == json.loads(DEFAULT_CUSTOM_FIELDS)

    def test_every_keycloak_owned_field_is_locked_on_screen(self):
        fields = json.loads(DEFAULT_CUSTOM_FIELDS)

        assert set(fields) == {"employee_id", "org_unit", "job_title"}
        assert all(field.get("disabled") is True for field in fields.values())


class TestStagingDeployment:
    """What a staging host needs from compose and Caddy: pinned release, replica set, real client IPs, locked /admin."""

    DEPLOY = MRC_CHAT_DIR / "mrc" / "deploy"

    @pytest.fixture(scope="class")
    def compose(self):
        return (self.DEPLOY / "compose.yaml").read_text(encoding="utf-8")

    def test_release_is_pinned_and_never_latest(self, compose):
        image = next(l for l in compose.splitlines() if "rocketchat/rocket.chat:" in l)

        assert image.strip().endswith(":${RELEASE:-8.9.0}")
        assert "latest" not in image
        assert "RELEASE=8.9.0" in (self.DEPLOY / ".env.example").read_text(encoding="utf-8")

    def test_database_uses_replica_set_rs0(self, compose):
        assert "--replSet" in compose and "rs0" in compose
        for variable in ("MONGO_URL", "MONGO_OPLOG_URL"):
            line = next(l for l in compose.splitlines() if f"- {variable}=" in l)
            assert "replicaSet=rs0" in line

    def test_root_url_has_no_production_fallback(self, compose):
        line = next(l for l in compose.splitlines() if "- ROOT_URL=" in l)

        assert "${ROOT_URL:?" in line, "a staging host must not silently start with another environment's URL"

    def test_rocketchat_trusts_one_proxy_for_client_ip(self, compose):
        assert "HTTP_FORWARDED_COUNT=${HTTP_FORWARDED_COUNT:-1}" in compose

    def test_ports_are_bound_to_loopback_for_caddy(self, compose):
        assert '"127.0.0.1:3000:3000"' in compose
        assert '"127.0.0.1:27017:27017"' in compose

    def test_caddy_locks_admin_and_fails_closed(self):
        caddy = (self.DEPLOY / "caddy" / "Caddyfile.snippet").read_text(encoding="utf-8")

        assert "{$CHAT_DOMAIN}" in caddy
        assert "path_regexp (?i)^/admin" in caddy
        assert "not remote_ip 127.0.0.1 ::1 {$ADMIN_IP_RANGES:}" in caddy, "unset range must leave only localhost"
        assert "reverse_proxy 127.0.0.1:3000" in caddy


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
