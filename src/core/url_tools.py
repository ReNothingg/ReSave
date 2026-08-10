from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

HTTP_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
BARE_URL_RE = re.compile(
    r"(?:www\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
    r"(?:/[^\s<>\"'`]*)?",
    re.IGNORECASE,
)


def _entity_url(text: str, entities) -> str | None:
    for entity in entities or []:
        if getattr(entity, "url", None):
            return str(entity.url)
        if getattr(entity, "type", None) == "url":
            extractor = getattr(entity, "extract_from", None)
            if callable(extractor):
                return str(extractor(text))
    return None


def normalize_url(value: str) -> str | None:
    candidate = value.strip().rstrip(".,;:!?)»”’]")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None

    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return None
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        address = None
    if address and not address.is_global:
        return None
    if address is None and ("." not in host or host.endswith((".local", ".internal", ".lan"))):
        return None

    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if any(not label or len(label) > 63 for label in ascii_host.split(".")):
        return None

    twitter_hosts = {
        "x.com",
        "www.x.com",
        "mobile.x.com",
        "m.x.com",
        "twitter.com",
        "www.twitter.com",
    }
    if ascii_host in twitter_hosts:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 3 and parts[1] in {"status", "statuses"} and parts[2].isdigit():
            return urlunsplit(
                (parsed.scheme, "twitter.com", f"/{parts[0]}/status/{parts[2]}", "", "")
            )

    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return None
    normalized_host = f"[{ascii_host}]" if address and address.version == 6 else ascii_host
    return urlunsplit(
        (
            parsed.scheme.lower(),
            f"{normalized_host}{port}",
            parsed.path or "/",
            parsed.query,
            "",
        )
    )


def extract_url(text: str, entities=None, caption_entities=None) -> str | None:
    raw = _entity_url(text, entities) or _entity_url(text, caption_entities)
    if raw is None:
        match = HTTP_URL_RE.search(text) or BARE_URL_RE.search(text)
        raw = match.group(0) if match else None
    return normalize_url(raw) if raw else None


async def is_public_url_target(url: str) -> bool:
    parsed = urlsplit(url)
    if not parsed.hostname:
        return False
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except (OSError, ValueError):
        return False
    resolved = {item[4][0] for item in addresses}
    return bool(resolved) and all(ipaddress.ip_address(item).is_global for item in resolved)
