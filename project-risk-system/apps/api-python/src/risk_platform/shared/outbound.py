"""SSRF-resistant DNS and destination validation for outbound integrations."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

from risk_platform.config import IpNetwork

IpAddress = IPv4Address | IPv6Address
Resolver = Callable[[str, int], Awaitable[Sequence[str]]]
EndpointKind = Literal["provider", "imap"]

_FORBIDDEN_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)
_FORBIDDEN_ADDRESSES = frozenset(
    {
        ip_address("169.254.169.254"),
        ip_address("fd00:ec2::254"),
    }
)


class OutboundSecurityError(RuntimeError):
    """A caller-safe endpoint error that omits the rejected destination."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class OutboundPolicy:
    """Explicit exceptions for approved internal endpoints.

    Both hostname and resolved address range must be approved.  Dangerous special-use
    ranges (loopback, link-local/metadata, unspecified, multicast) are never allowlisted.
    """

    approved_internal_hostnames: frozenset[str] = frozenset()
    approved_internal_networks: tuple[IpNetwork, ...] = ()

    def __post_init__(self) -> None:
        normalized = frozenset(
            _normalize_hostname(value) for value in self.approved_internal_hostnames
        )
        object.__setattr__(self, "approved_internal_hostnames", normalized)


@dataclass(frozen=True, slots=True)
class ResolvedEndpoint:
    kind: EndpointKind
    hostname: str
    port: int
    addresses: tuple[IpAddress, ...]
    url: str | None = None

    @property
    def connection_address(self) -> str:
        """Return the validated address a client should pin for its connection."""

        return str(self.addresses[0])


class OutboundEndpointGuard:
    """Validate, resolve, and revalidate endpoints immediately before connection."""

    def __init__(
        self,
        policy: OutboundPolicy | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._policy = policy or OutboundPolicy()
        self._resolver = resolver or system_resolver

    async def resolve_provider(self, endpoint: str) -> ResolvedEndpoint:
        try:
            parsed = urlsplit(endpoint)
            parsed_port = parsed.port
            port = 443 if parsed_port is None else parsed_port
        except ValueError:
            raise OutboundSecurityError("INVALID_PROVIDER_ENDPOINT") from None
        if (
            parsed.scheme.lower() != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or not 1 <= port <= 65_535
        ):
            raise OutboundSecurityError("INVALID_PROVIDER_ENDPOINT")
        hostname = _normalize_hostname(parsed.hostname)
        canonical = urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), parsed.query, ""))
        addresses = await self._resolve_allowed(hostname, port)
        return ResolvedEndpoint("provider", hostname, port, addresses, canonical)

    async def resolve_imap(self, host: str, port: int) -> ResolvedEndpoint:
        if not 1 <= port <= 65_535 or any(character in host for character in "/?#@"):
            raise OutboundSecurityError("INVALID_IMAP_ENDPOINT")
        try:
            hostname = _normalize_hostname(host.strip("[]"))
        except OutboundSecurityError:
            raise OutboundSecurityError("INVALID_IMAP_ENDPOINT") from None
        addresses = await self._resolve_allowed(hostname, port)
        return ResolvedEndpoint("imap", hostname, port, addresses)

    async def revalidate(self, endpoint: ResolvedEndpoint) -> ResolvedEndpoint:
        """Detect DNS rebinding; callers invoke this immediately before every connect."""

        addresses = await self._resolve_allowed(endpoint.hostname, endpoint.port)
        if frozenset(addresses) != frozenset(endpoint.addresses):
            raise OutboundSecurityError("OUTBOUND_DNS_CHANGED")
        return ResolvedEndpoint(
            endpoint.kind,
            endpoint.hostname,
            endpoint.port,
            addresses,
            endpoint.url,
        )

    def validate_redirect(self, source: ResolvedEndpoint, location: str) -> None:
        """Reject redirects; integrations must use clients configured not to follow them."""

        del source, location
        raise OutboundSecurityError("OUTBOUND_REDIRECT_FORBIDDEN")

    async def _resolve_allowed(self, hostname: str, port: int) -> tuple[IpAddress, ...]:
        if _hostname_is_forbidden(hostname):
            raise OutboundSecurityError("OUTBOUND_DESTINATION_FORBIDDEN")
        try:
            raw_addresses = await self._resolver(hostname, port)
            addresses = tuple(dict.fromkeys(ip_address(value) for value in raw_addresses))
        except (OSError, UnicodeError, ValueError):
            raise OutboundSecurityError("OUTBOUND_DNS_FAILED") from None
        if not addresses:
            raise OutboundSecurityError("OUTBOUND_DNS_FAILED")
        if any(not self._address_is_allowed(hostname, address) for address in addresses):
            raise OutboundSecurityError("OUTBOUND_DESTINATION_FORBIDDEN")
        return addresses

    def _address_is_allowed(self, hostname: str, address: IpAddress) -> bool:
        comparable = address.ipv4_mapped if isinstance(address, IPv6Address) else None
        checked = comparable or address
        if (
            address in _FORBIDDEN_ADDRESSES
            or checked in _FORBIDDEN_ADDRESSES
            or checked.is_loopback
            or checked.is_link_local
            or checked.is_unspecified
            or checked.is_multicast
            or checked.is_reserved
        ):
            return False
        if checked.is_global:
            return True
        return hostname in self._policy.approved_internal_hostnames and any(
            checked in network for network in self._policy.approved_internal_networks
        )


async def system_resolver(hostname: str, port: int) -> Sequence[str]:
    """Resolve all stream addresses without blocking the event loop."""

    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        hostname,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    return tuple(record[4][0] for record in records)


def provider_subresource_url(endpoint: ResolvedEndpoint, relative_path: str) -> str:
    """Build a same-origin path without permitting authority replacement."""

    if endpoint.kind != "provider" or endpoint.url is None or relative_path.startswith("//"):
        raise OutboundSecurityError("INVALID_PROVIDER_PATH")
    joined = urljoin(f"{endpoint.url.rstrip('/')}/", relative_path.lstrip("/"))
    parsed = urlsplit(joined)
    if parsed.hostname != endpoint.hostname or (parsed.port or 443) != endpoint.port:
        raise OutboundSecurityError("INVALID_PROVIDER_PATH")
    return joined


def _normalize_hostname(hostname: str) -> str:
    normalized = hostname.rstrip(".").lower()
    if not normalized or any(character.isspace() for character in normalized):
        raise OutboundSecurityError("INVALID_OUTBOUND_HOSTNAME")
    try:
        return normalized.encode("idna").decode("ascii")
    except UnicodeError:
        raise OutboundSecurityError("INVALID_OUTBOUND_HOSTNAME") from None


def _hostname_is_forbidden(hostname: str) -> bool:
    return (
        hostname in _FORBIDDEN_HOSTNAMES
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
    )


__all__ = [
    "OutboundEndpointGuard",
    "OutboundPolicy",
    "OutboundSecurityError",
    "ResolvedEndpoint",
    "Resolver",
    "provider_subresource_url",
    "system_resolver",
]
