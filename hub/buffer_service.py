"""Buffer GraphQL API integration for the Hub marketing planner."""

from __future__ import annotations

import json
import logging
from ipaddress import ip_address
from urllib.parse import urlparse

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

BUFFER_GRAPHQL_URL = "https://api.buffer.com"


class BufferServiceError(RuntimeError):
    """Raised for Buffer configuration, transport, or GraphQL errors."""


class BufferPartialFailure(BufferServiceError):
    """Raised when Buffer created some channel posts before a later failure."""

    def __init__(
        self,
        created_post_ids: list[str],
        created_profile_ids: list[str] | None = None,
    ):
        super().__init__("Buffer created only some requested channel posts")
        self.created_post_ids = created_post_ids
        self.created_profile_ids = created_profile_ids or []


def _is_public_media_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not hostname:
        return False
    if hostname == "localhost" or hostname.endswith(".local"):
        return False
    try:
        return ip_address(hostname).is_global
    except ValueError:
        return "." in hostname


def _graphql(query: str, variables: dict | None = None) -> dict:
    api_key = settings.BUFFER_API_KEY
    if not api_key:
        raise BufferServiceError("BUFFER_API_KEY is not configured")

    try:
        response = requests.post(
            BUFFER_GRAPHQL_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": variables or {}},
            timeout=settings.BUFFER_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.exception("Buffer GraphQL request failed")
        raise BufferServiceError("Buffer is temporarily unavailable") from exc

    if payload.get("errors"):
        message = payload["errors"][0].get("message", "Buffer GraphQL error")
        raise BufferServiceError(message)
    return payload.get("data") or {}


def _organization_id() -> str:
    configured = settings.BUFFER_ORGANIZATION_ID
    if configured:
        return configured

    data = _graphql("""
        query GetOrganizations {
          account { organizations { id name } }
        }
        """)
    organizations = data.get("account", {}).get("organizations", [])
    if len(organizations) == 1:
        return organizations[0]["id"]
    if not organizations:
        raise BufferServiceError("No Buffer organization is available for this API key")
    raise BufferServiceError(
        "BUFFER_ORGANIZATION_ID is required when the API key has multiple organizations"
    )


def list_buffer_profiles() -> list[dict]:
    """Fetch the connected Buffer channels for the configured organization."""

    organization_id = json.dumps(_organization_id())
    data = _graphql(f"""
        query GetChannels {{
          channels(input: {{ organizationId: {organization_id} }}) {{
            id
            name
            displayName
            service
            avatar
            isQueuePaused
          }}
        }}
        """)
    return [
        {
            "id": channel["id"],
            "service": channel.get("service", ""),
            "service_username": channel.get("name") or "",
            "avatar_url": channel.get("avatar") or None,
            "formatted_username": channel.get("displayName")
            or channel.get("name")
            or channel["id"],
            "is_queue_paused": bool(channel.get("isQueuePaused")),
        }
        for channel in data.get("channels", [])
    ]


def _create_channel_post(
    *, channel_id: str, text: str, scheduled_at: str | None, media_url: str | None
) -> str:
    post_input: dict = {
        "text": text,
        "channelId": channel_id,
        "schedulingType": "automatic",
        "mode": "customScheduled" if scheduled_at else "addToQueue",
        "aiAssisted": True,
        "source": "crush-hub",
        "assets": [],
    }
    if scheduled_at:
        post_input["dueAt"] = scheduled_at
    if media_url:
        if not _is_public_media_url(media_url):
            raise BufferServiceError(
                "Buffer media must use a publicly reachable HTTP(S) URL"
            )
        post_input["assets"] = [{"image": {"url": media_url}}]

    data = _graphql(
        """
        mutation CreatePost($input: CreatePostInput!) {
          createPost(input: $input) {
            __typename
            ... on PostActionSuccess { post { id status dueAt } }
            ... on MutationError { message }
          }
        }
        """,
        {"input": post_input},
    )
    result = data.get("createPost") or {}
    if result.get("__typename") != "PostActionSuccess":
        raise BufferServiceError(result.get("message") or "Buffer rejected the post")
    try:
        return result["post"]["id"]
    except (KeyError, TypeError) as exc:
        raise BufferServiceError("Buffer returned no post identifier") from exc


def create_buffer_update(
    *,
    text: str,
    profile_ids: list[str],
    scheduled_at: str | None = None,
    media_url: str | None = None,
) -> dict:
    """Create one Buffer post per selected channel."""

    if not profile_ids:
        raise BufferServiceError("Select at least one Buffer channel")

    post_ids = []
    created_profile_ids = []
    for channel_id in profile_ids:
        try:
            post_ids.append(
                _create_channel_post(
                    channel_id=channel_id,
                    text=text,
                    scheduled_at=scheduled_at,
                    media_url=media_url,
                )
            )
            created_profile_ids.append(channel_id)
        except BufferServiceError as exc:
            if post_ids:
                raise BufferPartialFailure(post_ids, created_profile_ids) from exc
            raise
    return {
        "success": True,
        "buffer_ids": post_ids,
        "buffer_id": ",".join(post_ids),
        "created_profile_ids": created_profile_ids,
    }
