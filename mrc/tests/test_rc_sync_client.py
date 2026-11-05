#!/usr/bin/env python3
"""Rocket.Chat REST client: authentication, retry policy and request shapes (section 12.6)."""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "integration" / "rc-sync"))

from rc_sync.client import (  # noqa: E402
    RETRY_DELAYS_SECONDS,
    RocketChatApiError,
    RocketChatClient,
    RocketChatUnavailable,
)


def make_client(handler):
    slept = []
    client = RocketChatClient(
        "http://rc.test/",
        "uid-1",
        "token-1",
        sleep=slept.append,
        transport=httpx.MockTransport(handler),
    )
    return client, slept


def ok(**body):
    return httpx.Response(200, json={"success": True, **body})


def test_sends_auth_headers_and_versioned_path():
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, user=request.headers["X-User-Id"], token=request.headers["X-Auth-Token"])
        return ok(user={"_id": "rc-1"})

    client, _ = make_client(handler)

    assert client.users_info(username="ngoc.tb") == {"_id": "rc-1"}
    assert seen == {"path": "/api/v1/users.info", "user": "uid-1", "token": "token-1"}


def test_retries_server_errors_with_documented_backoff_then_gives_up():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    client, slept = make_client(handler)

    with pytest.raises(RocketChatUnavailable):
        client.users_info(username="ngoc.tb")

    assert RETRY_DELAYS_SECONDS == (1, 5, 30, 120, 600)
    assert slept == list(RETRY_DELAYS_SECONDS)
    assert len(calls) == len(RETRY_DELAYS_SECONDS) + 1


def test_retries_network_errors_and_recovers():
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("refused", request=request)
        return ok(user={"_id": "rc-1"})

    client, slept = make_client(handler)

    assert client.users_info(username="ngoc.tb")["_id"] == "rc-1"
    assert slept == [1, 5]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_does_not_retry_client_errors(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"success": False, "errorType": "error-email-exists", "error": "Email is taken"})

    client, slept = make_client(handler)

    with pytest.raises(RocketChatApiError) as exc:
        client.users_update("rc-1", {"email": "a@b.c"})

    assert exc.value.status_code == status
    assert exc.value.error_type == "error-email-exists"
    assert len(calls) == 1 and slept == []


def test_errors_never_expose_the_token():
    def handler(request):
        return httpx.Response(500)

    client, _ = make_client(handler)

    with pytest.raises(RocketChatUnavailable) as exc:
        client.users_logout("rc-1")

    assert "token-1" not in str(exc.value)


def test_create_user_follows_pre_provisioning_rules():
    body = {}

    def handler(request):
        body.update(json.loads(request.content))
        return ok(user={"_id": "rc-1"})

    client, _ = make_client(handler)

    client.users_create({"username": "ngoc.tb", "name": "Trần Bảo Ngọc", "email": "n@m.vn"})

    assert len(body["password"]) == 40
    assert body["sendWelcomeEmail"] is False
    assert body["joinDefaultChannels"] is True


def test_create_user_uses_a_new_password_each_time():
    passwords = []

    def handler(request):
        passwords.append(json.loads(request.content)["password"])
        return ok(user={"_id": "rc-1"})

    client, _ = make_client(handler)

    client.users_create({"username": "a"})
    client.users_create({"username": "b"})

    assert passwords[0] != passwords[1]


@pytest.mark.parametrize("call, endpoint, expected", [
    (lambda c: c.users_set_active_status("u", False), "users.setActiveStatus", {"userId": "u", "activeStatus": False, "confirmRelinquish": True}),
    (lambda c: c.users_logout("u"), "users.logout", {"userId": "u"}),
    (lambda c: c.users_set_preferences("u", {"language": "vi-VN"}), "users.setPreferences", {"userId": "u", "data": {"language": "vi-VN"}}),
    (lambda c: c.users_set_avatar("u", "http://a"), "users.setAvatar", {"userId": "u", "avatarUrl": "http://a"}),
    (lambda c: c.teams_add_members("t", ["u"]), "teams.addMembers", {"teamId": "t", "members": [{"userId": "u", "roles": ["member"]}]}),
    (lambda c: c.teams_remove_member("t", "u"), "teams.removeMember", {"teamId": "t", "userId": "u"}),
    (lambda c: c.channels_invite("r", "u"), "channels.invite", {"roomId": "r", "userId": "u"}),
    (lambda c: c.channels_kick("r", "u"), "channels.kick", {"roomId": "r", "userId": "u"}),
    (lambda c: c.groups_invite("r", "u"), "groups.invite", {"roomId": "r", "userId": "u"}),
    (lambda c: c.groups_kick("r", "u"), "groups.kick", {"roomId": "r", "userId": "u"}),
])
def test_request_shapes(call, endpoint, expected):
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, body=json.loads(request.content))
        return ok()

    client, _ = make_client(handler)

    call(client)

    assert seen == {"path": f"/api/v1/{endpoint}", "body": expected}


def test_users_list_is_paginated():
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return ok(users=[], total=0)

    client, _ = make_client(handler)

    client.users_list(offset=100, count=50)

    assert seen == {"offset": "100", "count": "50"}
