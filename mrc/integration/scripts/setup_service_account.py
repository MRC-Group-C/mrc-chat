#!/usr/bin/env python3
"""
Create the rc-sync service account on Rocket.Chat (T-603, section 10.5).
Location: mrc-chat/mrc/integration/scripts/setup_service_account.py

Community workspaces cannot create custom roles (roles.create requires the
custom-roles license), so svc-rc-sync takes its permissions through the built-in
bot role, which is already exempt from the API rate limiter. Every bot account
therefore shares these permissions: notification senders must use webhooks.

The personal access token is written to --env-file and never printed.
Running the script again rotates the token.
"""

import argparse
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_sso import RocketChatAdminClient  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SERVICE_ROLE = "bot"
TOKEN_NAME = "rc-sync"

SERVICE_PERMISSIONS = [
    "create-user",
    "edit-other-user-info",
    "edit-other-user-active-status",
    "view-full-other-user-info",
    "logout-other-user",
    "assign-roles",
    "add-user-to-any-c-room",
    "add-user-to-any-p-room",
    "create-team",
    "edit-team-member",
    "view-other-user-channels",
    "create-personal-access-tokens",
]


def permissions_to_grant(current: Dict[str, List[str]], wanted: List[str], role: str) -> List[Dict[str, object]]:
    """Return permission updates that add role, keeping the roles already holding each one."""
    missing = [name for name in wanted if name not in current]
    if missing:
        raise ValueError(f"unknown permissions on this Rocket.Chat version: {', '.join(missing)}")
    return [
        {"_id": name, "roles": current[name] + [role]}
        for name in wanted
        if role not in current[name]
    ]


def upsert_env_values(content: str, values: Dict[str, str]) -> str:
    """Set KEY=value lines in dotenv text, replacing existing keys and keeping everything else."""
    for key, value in values.items():
        line = f"{key}={value}"
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        if pattern.search(content):
            content = pattern.sub(lambda _: line, content)
        else:
            content = f"{content.rstrip()}\n{line}\n"
    return content


def api_error(body: Dict[str, object]) -> str:
    return str(body.get("error") or body.get("message") or body)


def grant_permissions(admin: RocketChatAdminClient) -> int:
    status, body = admin._request("GET", "/api/v1/permissions.listAll")
    if status != 200:
        raise RuntimeError(f"permissions.listAll failed ({status}): {api_error(body)}")
    current = {p["_id"]: p.get("roles", []) for p in body.get("update", [])}

    updates = permissions_to_grant(current, SERVICE_PERMISSIONS, SERVICE_ROLE)
    if updates:
        status, body = admin._request("POST", "/api/v1/permissions.update", {"permissions": updates})
        if status != 200:
            raise RuntimeError(f"permissions.update failed ({status}): {api_error(body)}")
    return len(updates)


def ensure_user(admin: RocketChatAdminClient, username: str, password: str) -> str:
    """Create the bot, or reset its throwaway password when it already exists."""
    status, body = admin._request("GET", f"/api/v1/users.info?username={username}")
    if status == 200 and body.get("user"):
        user_id = body["user"]["_id"]
        status, body = admin._request("POST", "/api/v1/users.update", {"userId": user_id, "data": {"password": password}})
        if status != 200:
            raise RuntimeError(f"users.update failed ({status}): {api_error(body)}")
        return user_id

    status, body = admin._request("POST", "/api/v1/users.create", {
        "username": username,
        "name": "Dịch vụ đồng bộ rc-sync",
        "email": f"{username}@noemail.miraclegroup.vn",
        "password": password,
        "roles": [SERVICE_ROLE],
        "verified": True,
        "joinDefaultChannels": False,
        "requirePasswordChange": False,
        "sendWelcomeEmail": False,
    })
    if status != 200:
        raise RuntimeError(f"users.create failed ({status}): {api_error(body)}")
    return body["user"]["_id"]


def issue_token(base_url: str, username: str, password: str) -> Tuple[str, str]:
    """Log in as the service account and generate, or rotate, its personal access token."""
    client = RocketChatAdminClient(base_url)
    if not client.login(username, password):
        raise RuntimeError(f"login failed for '{username}'")
    try:
        data = {"tokenName": TOKEN_NAME, "bypassTwoFactor": True}
        status, body = client._request("POST", "/api/v1/users.generatePersonalAccessToken", data)
        if status != 200 and "exist" in api_error(body).lower():
            status, body = client._request("POST", "/api/v1/users.regeneratePersonalAccessToken", {"tokenName": TOKEN_NAME})
        if status != 200:
            raise RuntimeError(f"creating the access token failed ({status}): {api_error(body)}")
        return client.user_id, body["token"]
    finally:
        client.logout()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the rc-sync service account and access token")
    parser.add_argument("--rc-url", default=os.getenv("RC_URL", "http://localhost:3000"), help="Rocket.Chat base URL")
    parser.add_argument("--admin-user", default=os.getenv("ADM_LOCAL_USERNAME", "adm-local-chat"), help="Administrator username")
    parser.add_argument("--admin-password", default=os.getenv("ADM_LOCAL_PASSWORD", ""), help="Administrator password")
    parser.add_argument("--username", default=os.getenv("SVC_RC_SYNC_USERNAME", "svc-rc-sync"), help="Service account username")
    parser.add_argument("--env-file", required=True, type=Path, help="Dotenv file that receives RC_SYNC_USER_ID and RC_SYNC_TOKEN")
    args = parser.parse_args()

    if not args.admin_password:
        parser.error("missing required secret: --admin-password (ADM_LOCAL_PASSWORD)")
    if not args.env_file.is_file():
        parser.error(f"env file not found: {args.env_file}")

    admin = RocketChatAdminClient(args.rc_url)
    if not admin.login(args.admin_user, args.admin_password):
        print(f"[ERROR] Administrator login failed for '{args.admin_user}'")
        return 1

    try:
        granted = grant_permissions(admin)
        print(f"[OK] Role '{SERVICE_ROLE}': {granted} permission(s) added, {len(SERVICE_PERMISSIONS)} required")

        # The password only exists to obtain the token; it is never stored.
        password = secrets.token_urlsafe(30)
        ensure_user(admin, args.username, password)
        user_id, token = issue_token(args.rc_url, args.username, password)
    except (RuntimeError, ValueError, ConnectionError) as e:
        print(f"[ERROR] {e}")
        return 1
    finally:
        admin.logout()

    content = args.env_file.read_text(encoding="utf-8")
    args.env_file.write_text(
        upsert_env_values(content, {"RC_SYNC_USER_ID": user_id, "RC_SYNC_TOKEN": token}),
        encoding="utf-8",
    )
    print(f"[OK] Token for '{args.username}' written to {args.env_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
