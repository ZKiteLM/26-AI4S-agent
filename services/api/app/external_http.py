"""Bounded HTTP reads for public academic APIs and paper project pages."""

import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit

import httpx


class ExternalError(Exception):
    pass


def public_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 443) or parsed.hostname.endswith((".local", ".internal")):
            return False
        if parsed.hostname in ("localhost", "metadata.google.internal"):
            return False
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(
            ipaddress.ip_address(item[4][0]).is_global for item in addresses
        )
    except (ValueError, OSError):
        return False


def get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    max_bytes: int = 1_000_000,
    retries: int = 2,
) -> httpx.Response:
    """Read a bounded public HTTPS response, validating every redirect destination."""
    if params:
        url = str(httpx.URL(url).copy_merge_params(params))
    with httpx.Client(timeout=httpx.Timeout(12, connect=5), follow_redirects=False) as client:
        for attempt in range(retries + 1):
            current = url
            try:
                for _ in range(4):
                    if not public_url(current):
                        raise ExternalError("Blocked non-public URL")
                    with client.stream("GET", current, headers=headers) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            destination = response.headers.get("location")
                            if not destination:
                                raise ExternalError("Redirect without location")
                            current = urljoin(current, destination)
                            continue
                        if response.status_code in (429, 500, 502, 503, 504):
                            if attempt < retries:
                                delay = min(4.0, 0.4 * (2**attempt))
                                if response.headers.get("retry-after", "").isdigit():
                                    delay = min(
                                        4.0, max(delay, float(response.headers["retry-after"]))
                                    )
                                time.sleep(delay)
                                break
                            raise ExternalError(f"HTTP {response.status_code}")
                        response.raise_for_status()
                        if int(response.headers.get("content-length", "0")) > max_bytes:
                            raise ExternalError("Response too large")
                        chunks, size = [], 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                raise ExternalError("Response too large")
                            chunks.append(chunk)
                        safe_headers = dict(response.headers)
                        safe_headers.pop("content-encoding", None)
                        safe_headers.pop("content-length", None)
                        return httpx.Response(
                            response.status_code,
                            headers=safe_headers,
                            content=b"".join(chunks),
                            request=response.request,
                        )
                else:
                    raise ExternalError("Too many redirects")
            except httpx.HTTPStatusError as exc:
                raise ExternalError(str(exc)) from exc
            except (httpx.HTTPError, OSError) as exc:
                if attempt >= retries:
                    raise ExternalError(str(exc)) from exc
                time.sleep(0.4 * (2**attempt))
    raise ExternalError("Request failed")


def json_get(url: str, **kwargs) -> dict:
    try:
        data = get(url, **kwargs).json()
        if not isinstance(data, dict):
            raise TypeError("Expected JSON object")
        return data
    except (ValueError, TypeError) as exc:
        raise ExternalError("Invalid JSON response") from exc
