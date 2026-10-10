"""Static eligibility for the operational picker; never performs DNS or egress.

This is not a security boundary. The worker's existing public-URL checks remain
mandatory. Placeholder records stay addressable for history and diagnostics.
"""

from ipaddress import ip_address
from urllib.parse import urlsplit

PLACEHOLDER_DOMAINS = ("example.com", "example.net", "example.org", "invalid", "test", "localhost", "local")


def is_operational_source_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return False
        host = (parsed.hostname or "").lower().rstrip(".")
        _ = parsed.port  # malformed/out-of-range ports are not selectable
        if not host or any(host == d or host.endswith("." + d) for d in PLACEHOLDER_DOMAINS):
            return False
        try:
            address = ip_address(host)
        except ValueError:
            return "." in host
        return address.is_global and not address.is_multicast and not address.is_reserved
    except (TypeError, ValueError):
        return False
