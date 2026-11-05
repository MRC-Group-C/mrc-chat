"""Rocket.Chat REST client for rc-sync (section 12.6).

Network failures and 5xx responses are retried with the backoff of section 12.6.
4xx responses are business errors (a duplicate email, for instance): they are raised
at once so they can go to the error queue for an administrator.
"""

import secrets
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

import httpx

RETRY_DELAYS_SECONDS = (1, 5, 30, 120, 600)


class RocketChatError(Exception):
    """Base class for failed Rocket.Chat calls."""


class RocketChatApiError(RocketChatError):
    """Rocket.Chat rejected the request; retrying will not help."""

    def __init__(self, status_code: int, error_type: str, message: str):
        super().__init__(f"HTTP {status_code} {error_type}: {message}")
        self.status_code = status_code
        self.error_type = error_type


class RocketChatUnavailable(RocketChatError):
    """Rocket.Chat stayed unreachable or failing through every retry."""


class RocketChatClient:
    def __init__(
        self,
        base_url: str,
        user_id: str,
        token: str,
        timeout: float = 15.0,
        retry_delays: Sequence[float] = RETRY_DELAYS_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self._http = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/api/v1/",
            headers={"X-User-Id": user_id, "X-Auth-Token": token},
            timeout=timeout,
            transport=transport,
        )
        self._retry_delays = retry_delays
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, endpoint: str, *, params: Optional[Dict[str, Any]] = None, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        failure = ""
        for delay in (*self._retry_delays, None):
            try:
                response = self._http.request(method, endpoint, params=params, json=json)
            except httpx.TransportError as e:
                failure = f"{type(e).__name__}"
            else:
                if response.status_code < 500:
                    return self._parse(response)
                failure = f"HTTP {response.status_code}"
            if delay is not None:
                self._sleep(delay)
        raise RocketChatUnavailable(f"{method} {endpoint} failed after retries: {failure}")

    @staticmethod
    def _parse(response: httpx.Response) -> Dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or body.get("success") is False:
            raise RocketChatApiError(
                response.status_code,
                str(body.get("errorType") or "error"),
                str(body.get("error") or body.get("message") or response.reason_phrase),
            )
        return body

    # --- users ---------------------------------------------------------------

    def users_info(self, user_id: Optional[str] = None, username: Optional[str] = None) -> Dict[str, Any]:
        params = {"userId": user_id} if user_id else {"username": username}
        return self._call("GET", "users.info", params=params)["user"]

    def users_list(self, offset: int = 0, count: int = 100) -> Dict[str, Any]:
        return self._call("GET", "users.list", params={"offset": offset, "count": count})

    def users_create(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Pre-provision an account (CHT-LU-02). Its password is random and kept nowhere: login goes through SSO."""
        body = {
            **user_data,
            "password": secrets.token_urlsafe(40)[:40],
            "sendWelcomeEmail": False,
            "joinDefaultChannels": True,
            "requirePasswordChange": False,
        }
        return self._call("POST", "users.create", json=body)["user"]

    def users_update(self, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("POST", "users.update", json={"userId": user_id, "data": data})["user"]

    def users_set_active_status(self, user_id: str, active: bool) -> Dict[str, Any]:
        return self._call("POST", "users.setActiveStatus", json={"userId": user_id, "activeStatus": active, "confirmRelinquish": True})

    def users_set_preferences(self, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("POST", "users.setPreferences", json={"userId": user_id, "data": data})

    def users_logout(self, user_id: str) -> Dict[str, Any]:
        return self._call("POST", "users.logout", json={"userId": user_id})

    def users_set_avatar(self, user_id: str, avatar_url: str) -> Dict[str, Any]:
        return self._call("POST", "users.setAvatar", json={"userId": user_id, "avatarUrl": avatar_url})

    # --- rooms ---------------------------------------------------------------

    def teams_add_members(self, team_id: str, user_ids: List[str]) -> Dict[str, Any]:
        members = [{"userId": user_id, "roles": ["member"]} for user_id in user_ids]
        return self._call("POST", "teams.addMembers", json={"teamId": team_id, "members": members})

    def teams_remove_member(self, team_id: str, user_id: str) -> Dict[str, Any]:
        return self._call("POST", "teams.removeMember", json={"teamId": team_id, "userId": user_id})

    def channels_invite(self, room_id: str, user_id: str) -> Dict[str, Any]:
        return self._call("POST", "channels.invite", json={"roomId": room_id, "userId": user_id})

    def channels_kick(self, room_id: str, user_id: str) -> Dict[str, Any]:
        return self._call("POST", "channels.kick", json={"roomId": room_id, "userId": user_id})

    def groups_invite(self, room_id: str, user_id: str) -> Dict[str, Any]:
        return self._call("POST", "groups.invite", json={"roomId": room_id, "userId": user_id})

    def groups_kick(self, room_id: str, user_id: str) -> Dict[str, Any]:
        return self._call("POST", "groups.kick", json={"roomId": room_id, "userId": user_id})
