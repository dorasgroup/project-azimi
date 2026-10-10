"""Small fail-closed HTTP CONNECT proxy with DNS-over-HTTPS resolution."""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import os
import ssl
import struct
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlsplit


LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = int(os.getenv("EGRESS_PORT", "8080"))
DOH_ADDRESS = os.getenv("EGRESS_DOH_ADDRESS", "8.8.8.8")
DOH_HOST = os.getenv("EGRESS_DOH_HOST", "dns.google")
SOCKS_PROXY = os.getenv("EGRESS_SOCKS_PROXY", "").strip()
MAX_HEADER_BYTES = 65_536
ALLOWED_PORTS = {80, 443}


class Rejected(ValueError):
    pass


def log_event(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, separators=(",", ":")), flush=True)


def public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def normalize_target(host: str, port: int) -> tuple[str, int]:
    try:
        normalized = host.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise Rejected("invalid host") from exc
    if not normalized or len(normalized) > 253 or port not in ALLOWED_PORTS:
        raise Rejected("target is not allowed")
    if normalized == "localhost" or normalized.endswith(
        (".localhost", ".local", ".internal")
    ):
        raise Rejected("target is not public")
    try:
        literal = ipaddress.ip_address(normalized)
    except ValueError:
        literal = None
    if literal is not None and not public_ip(str(literal)):
        raise Rejected("target is not public")
    return normalized, port


@dataclass
class CacheEntry:
    addresses: tuple[str, ...]
    expires_at: float


class DnsOverHttpsResolver:
    def __init__(self) -> None:
        self._cache: dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _query(host: str) -> tuple[tuple[str, ...], int]:
        context = ssl.create_default_context()
        connection = http.client.HTTPSConnection(
            DOH_ADDRESS,
            443,
            timeout=10,
            context=context,
        )
        try:
            connection.request(
                "GET",
                f"/resolve?name={quote(host, safe='')}&type=A",
                headers={"Host": DOH_HOST, "Accept": "application/dns-json"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise Rejected("resolver unavailable")
            payload = json.loads(response.read(131_072))
        finally:
            connection.close()
        if payload.get("Status") != 0:
            raise Rejected("host resolution failed")
        answers = payload.get("Answer") or []
        addresses = tuple(
            dict.fromkeys(
                str(answer.get("data"))
                for answer in answers
                if answer.get("type") == 1 and answer.get("data")
            )
        )
        if not addresses or any(not public_ip(address) for address in addresses):
            raise Rejected("host does not resolve exclusively to public addresses")
        ttls = [int(answer.get("TTL", 60)) for answer in answers if answer.get("type") == 1]
        return addresses, max(30, min(min(ttls or [60]), 300))

    async def resolve(self, host: str) -> tuple[str, ...]:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if not public_ip(str(literal)):
                raise Rejected("target is not public")
            return (str(literal),)

        now = time.monotonic()
        cached = self._cache.get(host)
        if cached and cached.expires_at > now:
            return cached.addresses
        async with self._lock:
            now = time.monotonic()
            cached = self._cache.get(host)
            if cached and cached.expires_at > now:
                return cached.addresses
            addresses, ttl = await asyncio.to_thread(self._query, host)
            self._cache[host] = CacheEntry(addresses, now + ttl)
            return addresses


RESOLVER = DnsOverHttpsResolver()


async def send_response(
    writer: asyncio.StreamWriter,
    status: str,
    body: bytes = b"",
) -> None:
    writer.write(
        f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\nContent-Type: text/plain\r\n\r\n".encode("ascii")
        + body
    )
    await writer.drain()


async def open_via_socks(
    address: str,
    port: int,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    proxy = urlsplit(f"//{SOCKS_PROXY}")
    if not proxy.hostname or not proxy.port:
        raise Rejected("egress tunnel is not configured")
    username = proxy.username
    password = proxy.password
    if (username is None) != (password is None):
        raise Rejected("incomplete tunnel credentials")
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(proxy.hostname, proxy.port), timeout=12
    )
    try:
        methods = b"\x00\x02" if username is not None else b"\x00"
        writer.write(b"\x05" + bytes((len(methods),)) + methods)
        await writer.drain()
        negotiation = await reader.readexactly(2)
        if negotiation == b"\x05\x02" and username is not None and password is not None:
            username_bytes = username.encode("utf-8")
            password_bytes = password.encode("utf-8")
            if not 1 <= len(username_bytes) <= 255 or len(password_bytes) > 255:
                raise Rejected("invalid tunnel credentials")
            writer.write(
                b"\x01"
                + bytes((len(username_bytes),))
                + username_bytes
                + bytes((len(password_bytes),))
                + password_bytes
            )
            await writer.drain()
            if await reader.readexactly(2) != b"\x01\x00":
                raise Rejected("egress tunnel rejected authentication")
        elif negotiation != b"\x05\x00":
            raise Rejected("egress tunnel rejected authentication")
        packed = ipaddress.ip_address(address).packed
        address_type = b"\x01" if len(packed) == 4 else b"\x04"
        writer.write(b"\x05\x01\x00" + address_type + packed + struct.pack("!H", port))
        await writer.drain()
        response = await reader.readexactly(4)
        if response[:2] != b"\x05\x00":
            raise Rejected("egress tunnel rejected destination")
        response_type = response[3]
        if response_type == 1:
            await reader.readexactly(4)
        elif response_type == 4:
            await reader.readexactly(16)
        elif response_type == 3:
            await reader.readexactly((await reader.readexactly(1))[0])
        else:
            raise Rejected("invalid tunnel response")
        await reader.readexactly(2)
        return reader, writer
    except Exception:
        writer.close()
        raise


async def open_public(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    host, port = normalize_target(host, port)
    addresses = await RESOLVER.resolve(host)
    last_error: OSError | None = None
    for address in addresses:
        try:
            if SOCKS_PROXY:
                return await open_via_socks(address, port)
            return await asyncio.wait_for(
                asyncio.open_connection(address, port), timeout=12
            )
        except OSError as exc:
            last_error = exc
    raise Rejected("upstream connection failed") from last_error


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65_536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        if writer.can_write_eof():
            try:
                writer.write_eof()
            except OSError:
                pass


def parse_headers(raw: bytes) -> tuple[str, str, str, list[tuple[str, str]]]:
    try:
        lines = raw.decode("iso-8859-1").split("\r\n")
        method, target, version = lines[0].split(" ", 2)
        headers = []
        for line in lines[1:]:
            if not line:
                continue
            name, value = line.split(":", 1)
            headers.append((name.strip(), value.strip()))
        return method.upper(), target, version, headers
    except (ValueError, UnicodeError) as exc:
        raise Rejected("malformed request") from exc


async def handle_control(target: str, writer: asyncio.StreamWriter) -> None:
    parsed = urlsplit(target)
    if parsed.path == "/__egress__/health":
        await send_response(writer, "204 No Content")
        return
    if parsed.path != "/__egress__/resolve":
        await send_response(writer, "404 Not Found")
        return
    query = parse_qs(parsed.query)
    try:
        host = query["host"][0]
        port = int(query["port"][0])
        host, port = normalize_target(host, port)
        await RESOLVER.resolve(host)
    except (KeyError, ValueError, Rejected):
        await send_response(writer, "403 Forbidden")
        return
    await send_response(writer, "204 No Content")


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    started = time.monotonic()
    host = "unknown"
    port = 0
    outcome = "rejected"
    try:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        if len(raw) > MAX_HEADER_BYTES:
            raise Rejected("headers too large")
        method, target, version, headers = parse_headers(raw)
        if target.startswith("/__egress__/"):
            await handle_control(target, writer)
            outcome = "control"
            return

        if method == "CONNECT":
            authority = urlsplit(f"//{target}")
            if not authority.hostname:
                raise Rejected("invalid CONNECT target")
            host, port = normalize_target(authority.hostname, authority.port or 443)
            upstream_reader, upstream_writer = await open_public(host, port)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            outcome = "connected"
            await asyncio.gather(
                relay(reader, upstream_writer),
                relay(upstream_reader, writer),
            )
            return

        if method not in {"GET", "HEAD"}:
            raise Rejected("method is not allowed")
        parsed = urlsplit(target)
        if parsed.scheme != "http" or not parsed.hostname:
            raise Rejected("absolute HTTP target required")
        host, port = normalize_target(parsed.hostname, parsed.port or 80)
        upstream_reader, upstream_writer = await open_public(host, port)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        safe_headers = [
            (name, value)
            for name, value in headers
            if name.casefold() not in {"proxy-authorization", "proxy-connection", "connection"}
        ]
        request = [f"{method} {path} {version}", *(f"{n}: {v}" for n, v in safe_headers)]
        upstream_writer.write(("\r\n".join(request) + "\r\nConnection: close\r\n\r\n").encode("iso-8859-1"))
        await upstream_writer.drain()
        outcome = "connected"
        await relay(upstream_reader, writer)
        upstream_writer.close()
        return
    except (Rejected, asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
        if not writer.is_closing():
            await send_response(writer, "403 Forbidden")
    except Exception:
        if not writer.is_closing():
            await send_response(writer, "502 Bad Gateway")
        outcome = "error"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        if outcome != "control":
            log_event(
                "egress_request",
                host=host,
                port=port,
                outcome=outcome,
                latency_ms=round((time.monotonic() - started) * 1000),
            )


async def main() -> None:
    server = await asyncio.start_server(
        handle_client,
        LISTEN_HOST,
        LISTEN_PORT,
        limit=MAX_HEADER_BYTES + 1,
    )
    log_event(
        "egress_ready",
        port=LISTEN_PORT,
        resolver=DOH_HOST,
        tunnel="socks5" if SOCKS_PROXY else "direct",
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
