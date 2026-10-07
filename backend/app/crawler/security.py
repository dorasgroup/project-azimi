import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    pass


def _is_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUrlError("Only public HTTP and HTTPS URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("Credentials in URLs are not allowed")
    if parsed.hostname.lower() == "localhost":
        raise UnsafeUrlError("Localhost is not allowed")

    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Host resolution failed: {parsed.hostname}") from exc

    addresses = {record[4][0] for record in records}
    if not addresses or any(not _is_public_ip(address) for address in addresses):
        raise UnsafeUrlError("URL resolves to a non-public address")
    return url


def host_is_allowed(url: str, source_url: str, allow_subdomains: bool = False) -> bool:
    host = (urlparse(url).hostname or "").lower()
    source_host = (urlparse(source_url).hostname or "").lower()
    return host == source_host or (allow_subdomains and host.endswith(f".{source_host}"))
