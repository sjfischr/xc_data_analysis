"""Outbound URL and network destination policy (Requirement 4.8).

Shared by every adapter that fetches an administrator-influenced URL: the
RunSignup adapter (Task 8) constructs REST calls from a race ID an admin
supplied, and Tavily discovery (Task 9) crawls domains an admin approved.
Both need the same defense, so it lives here once rather than being
reimplemented per adapter.

Four checks, in order, each closing a distinct SSRF avenue:

1. **Scheme** -- HTTPS only.
2. **Host allowlist** -- exact host or subdomain of an approved domain; no
   wildcard TLD matching.
3. **Port** -- only 443.
4. **DNS resolution** -- the hostname must resolve to a public IP address;
   loopback, private-range (RFC 1918/RFC 4193), link-local, multicast,
   reserved, unspecified, and the cloud metadata address
   (``169.254.169.254``) are all refused. A hostname that resolves
   differently on a later call (DNS rebinding) is caught because this
   check runs again on every redirect hop, not only on the initial URL.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from socket import AddressFamily, SocketKind
from urllib.parse import urlsplit

# The RunSignup REST API and its two known white-label front ends (design.md
# section 9.2, confirmed live Task 3.3): all three serve the same numeric
# race IDs, so a URL on any of them refers to the same underlying source.
RUNSIGNUP_FAMILY_HOSTS: frozenset[str] = frozenset(
    {"runsignup.com", "trisignup.com", "adventuresignup.com"}
)

_ALLOWED_SCHEMES = frozenset({"https"})
_ALLOWED_PORTS = frozenset({443})
_METADATA_SERVICE_IPS = frozenset({"169.254.169.254", "fd00:ec2::254"})

# socket.getaddrinfo(host, port, type=socket.SOCK_STREAM) -> list of
# (family, type, proto, canonname, sockaddr) tuples; sockaddr[0] is the IP.
_AddrInfo = tuple[
    AddressFamily, SocketKind, int, str, tuple[str, int] | tuple[str, int, int, int]
]
_Resolver = Callable[[str, int], list[_AddrInfo]]


class URLPolicyError(ValueError):
    """A URL or its resolved network destination is not permitted."""


@dataclass(frozen=True, slots=True)
class ResolvedDestination:
    """The outcome of a passed policy check, for logging/provenance."""

    url: str
    host: str
    port: int
    resolved_ip: str


def _default_resolver(host: str, port: int) -> list[_AddrInfo]:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    return str(ip) not in _METADATA_SERVICE_IPS


def _host_is_allowed(host: str, allowed_hosts: frozenset[str]) -> bool:
    return host in allowed_hosts or any(
        host.endswith(f".{allowed}") for allowed in allowed_hosts
    )


def validate_destination(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    resolver: _Resolver = _default_resolver,
) -> ResolvedDestination:
    """Validate ``url`` against every SSRF check. Raises on the first failure.

    Call this on the initial URL and again on every redirect hop's
    ``Location`` header -- a destination that passes here once is not
    trusted to still pass after a redirect.
    """
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise URLPolicyError(
            f"scheme {parts.scheme!r} is not allowed; only https is permitted"
        )

    host = parts.hostname
    if not host:
        raise URLPolicyError("URL has no host")
    host = host.lower()
    if not _host_is_allowed(host, allowed_hosts):
        raise URLPolicyError(
            f"host {host!r} is not in the approved allowlist {sorted(allowed_hosts)}"
        )

    port = parts.port or 443
    if port not in _ALLOWED_PORTS:
        raise URLPolicyError(f"port {port} is not allowed; only 443 is permitted")

    try:
        addr_infos = resolver(host, port)
    except OSError as exc:
        raise URLPolicyError(f"DNS resolution failed for {host!r}: {exc}") from exc
    if not addr_infos:
        raise URLPolicyError(f"DNS resolution returned no addresses for {host!r}")

    # Every resolved address must be public -- a hostname that resolves to
    # even one private/internal address alongside public ones is refused,
    # since the client library's own address selection is not something
    # this policy controls.
    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if not _is_public_ip(ip):
            raise URLPolicyError(
                f"host {host!r} resolved to a non-public address {ip}; refused"
            )

    resolved_ip = str(addr_infos[0][4][0])
    return ResolvedDestination(url=url, host=host, port=port, resolved_ip=resolved_ip)
