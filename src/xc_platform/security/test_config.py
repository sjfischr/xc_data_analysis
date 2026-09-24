"""Unit tests for :mod:`xc_platform.security.config`.

These tests use dummy environment variables only. They never read, reference,
or assert on any real credential.
"""

from __future__ import annotations

import pytest

from xc_platform.security import config


def test_get_secret_reads_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_secret returns the value of the matching environment variable."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DUMMY_TEST_SECRET", "dummy-value-123")

    assert config.get_secret("DUMMY_TEST_SECRET") == "dummy-value-123"


def test_get_secret_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing secret with no default raises SecretNotFoundError."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.delenv("DUMMY_MISSING_SECRET", raising=False)

    with pytest.raises(config.SecretNotFoundError):
        config.get_secret("DUMMY_MISSING_SECRET")


def test_get_secret_returns_default_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing secret returns the supplied default instead of raising."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.delenv("DUMMY_MISSING_SECRET", raising=False)

    assert config.get_secret("DUMMY_MISSING_SECRET", default="fallback") == "fallback"


def test_ssm_backend_reads_a_decrypted_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Task 18.1: the SSM backend is real -- requests decryption and
    applies the configured prefix, without ever needing a live AWS call
    (a fake client is injected in place of boto3's)."""
    monkeypatch.setenv("SECRET_BACKEND", "ssm")
    monkeypatch.setenv("XC_PLATFORM_SSM_PREFIX", "/xc-platform/")
    config._ssm_client = None

    class _FakeSsmClient:
        def get_parameter(self, *, Name: str, WithDecryption: bool) -> dict[str, object]:
            assert Name == "/xc-platform/DUMMY_TEST_SECRET"
            assert WithDecryption is True
            return {"Parameter": {"Value": "dummy-ssm-value"}}

    monkeypatch.setattr(config, "_get_ssm_client", lambda: _FakeSsmClient())

    assert config.get_secret("DUMMY_TEST_SECRET") == "dummy-ssm-value"


def test_ssm_backend_missing_parameter_raises_secret_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_BACKEND", "ssm")
    monkeypatch.setenv("XC_PLATFORM_SSM_PREFIX", "")
    config._ssm_client = None

    from botocore.exceptions import ClientError

    class _FakeSsmClient:
        def get_parameter(self, *, Name: str, WithDecryption: bool) -> dict[str, object]:
            raise ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": "not found"}},
                "GetParameter",
            )

    monkeypatch.setattr(config, "_get_ssm_client", lambda: _FakeSsmClient())

    with pytest.raises(config.SecretNotFoundError):
        config.get_secret("DUMMY_MISSING_SECRET")
