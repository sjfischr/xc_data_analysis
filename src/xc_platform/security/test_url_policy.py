from __future__ import annotations

import socket
from collections.abc import Callable

import pytest

from xc_platform.security.url_policy import (
    RUNSIGNUP_FAMILY_HOSTS,
    URLPolicyError,
    validate_destination,
)

_PUBLIC_IP = "1.1.1.1"  # unambiguously public in ipaddress' own classification

_AddrInfo = tuple[
    socket.AddressFamily,
    socket.SocketKind,
    int,
    str,
    tuple[str, int] | tuple[str, int, int, int],
]
_Resolver = Callable[[str, int], list[_AddrInfo]]


def _resolver_for(ip: str) -> _Resolver:
    def resolver(host: str, port: int) -> list[_AddrInfo]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    return resolver


def test_accepts_an_allowlisted_host_resolving_to_a_public_ip() -> None:
    result = validate_destination(
        "https://runsignup.com/Rest/race/1?format=json",
        allowed_hosts=RUNSIGNUP_FAMILY_HOSTS,
        resolver=_resolver_for(_PUBLIC_IP),
    )
    assert result.host == "runsignup.com"
    assert result.resolved_ip == _PUBLIC_IP


def test_accepts_a_subdomain_of_an_allowlisted_host() -> None:
    result = validate_destination(
        "https://www.runsignup.com/Rest/race/1?format=json",
        allowed_hosts=RUNSIGNUP_FAMILY_HOSTS,
        resolver=_resolver_for(_PUBLIC_IP),
    )
    assert result.host == "www.runsignup.com"


def test_accepts_mirror_hosts() -> None:
    for host in ("trisignup.com", "adventuresignup.com"):
        result = validate_destination(
            f"https://{host}/Race/Results/1",
            allowed_hosts=RUNSIGNUP_FAMILY_HOSTS,
            resolver=_resolver_for(_PUBLIC_IP),
        )
        assert result.host == host


def test_rejects_non_https_scheme() -> None:
    with pytest.raises(URLPolicyError, match="scheme"):
        validate_destination(
            "http://runsignup.com/",
            allowed_hosts=RUNSIGNUP_FAMILY_HOSTS,
            resolver=_resolver_for(_PUBLIC_IP),
        )


def test_rejects_a_host_not_on_the_allowlist() -> None:
    with pytest.raises(URLPolicyError, match="allowlist"):
        validate_destination(
            "https://evil.example.com/",
            allowed_hosts=RUNSIGNUP_FAMILY_HOSTS,
            resolver=_resolver_for(_PUBLIC_IP),
        )


def test_does_not_allow_a_host_that_merely_contains_the_allowed_domain() -> None:
    """A typosquat like runsignup.com.evil.example must not pass suffix matching."""
    with pytest.raises(URLPolicyError, match="allowlist"):
        validate_destination(
            "https://runsignup.com.evil.example/",
            allowed_hosts=RUNSIGNUP_FAMILY_HOSTS,
            resolver=_resolver_for(_PUBLIC_IP),
        )


def test_rejects_a_non_standard_port() -> None:
    with pytest.raises(URLPolicyError, match="port"):
        validate_destination(
            "https://runsignup.com:8443/",
            allowed_hosts=RUNSIGNUP_FAMILY_HOSTS,
            resolver=_resolver_for(_PUBLIC_IP),
        )


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # RFC 1918 private
        "192.168.1.1",  # RFC 1918 private
        "172.16.0.1",  # RFC 1918 private
        "169.254.169.254",  # cloud metadata service
        "169.254.1.1",  # link-local
        "224.0.0.1",  # multicast
        "0.0.0.0",  # noqa: S104 -- unspecified address, not a bind call
    ],
)
def test_rejects_every_non_public_ip_family(ip: str) -> None:
    with pytest.raises(URLPolicyError, match="non-public"):
        validate_destination(
            "https://runsignup.com/",
            allowed_hosts=RUNSIGNUP_FAMILY_HOSTS,
            resolver=_resolver_for(ip),
        )


def test_rejects_when_any_resolved_address_is_non_public() -> None:
    """DNS can return multiple addresses; every one must be public."""

    def resolver(host: str, port: int) -> list[_AddrInfo]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port)),
        ]

    with pytest.raises(URLPolicyError, match="non-public"):
        validate_destination(
            "https://runsignup.com/",
            allowed_hosts=RUNSIGNUP_FAMILY_HOSTS,
            resolver=resolver,
        )


def test_rejects_dns_resolution_failure() -> None:
    def resolver(host: str, port: int) -> list[_AddrInfo]:
        raise OSError("name resolution failed")

    with pytest.raises(URLPolicyError, match="DNS resolution failed"):
        validate_destination(
            "https://runsignup.com/",
            allowed_hosts=RUNSIGNUP_FAMILY_HOSTS,
            resolver=resolver,
        )


def test_rejects_a_url_with_no_host() -> None:
    with pytest.raises(URLPolicyError, match="no host"):
        validate_destination(
            "https:///path",
            allowed_hosts=RUNSIGNUP_FAMILY_HOSTS,
            resolver=_resolver_for(_PUBLIC_IP),
        )
