"""Secret resolution for the XC Data Platform.

This module is the single, documented entry point for reading credentials
(such as the Tavily API key) and other sensitive configuration values. It
replaces the legacy pattern of reading a bare ``tavily_api_key`` file from the
repository root.

Resolution strategy (Requirement 16.2):

* **Local development** — secrets are read from environment variables. Copy
  ``.env.example`` to ``.env``, fill in real values there (``.env`` is
  git-ignored), and load it into the process environment before running.
* **Deployed AWS environments** — secrets are read from SSM Parameter Store
  SecureString (or another approved AWS secret service). The AWS path is a
  documented hook below; it is intentionally a clearly-marked stub at this
  stage and is wired up during the feasibility / infrastructure tasks.

Design invariants:

* No secret value is ever hardcoded, logged, or written back to disk here.
* Callers reference secrets by *name*, never by embedding the value.
* The backend is selected by the ``SECRET_BACKEND`` environment variable
  (``"env"`` by default, ``"ssm"`` in AWS) so the same call site works in both
  environments.
"""

from __future__ import annotations

import os
from typing import Any

# Backend selector. "env" resolves from environment variables (local dev);
# "ssm" resolves from AWS SSM Parameter Store SecureString (deployed).
_BACKEND_ENV_VAR = "SECRET_BACKEND"
_DEFAULT_BACKEND = "env"

# Optional prefix applied to parameter names when using the SSM backend, e.g.
# a name of "TAVILY_API_KEY" under prefix "/xc-platform/" resolves the
# parameter "/xc-platform/TAVILY_API_KEY".
_SSM_PREFIX_ENV_VAR = "XC_PLATFORM_SSM_PREFIX"


class SecretNotFoundError(KeyError):
    """Raised when a requested secret cannot be resolved.

    The error message references only the secret *name*, never its value.
    """


def _resolve_backend() -> str:
    return os.environ.get(_BACKEND_ENV_VAR, _DEFAULT_BACKEND).strip().lower()


def _get_from_env(name: str) -> str | None:
    """Resolve a secret from an environment variable of the same name."""
    return os.environ.get(name)


_ssm_client: Any | None = None


def _get_ssm_client() -> Any:
    """Lazily constructed and cached (Task 18.1) -- module-scope caching is
    safe here the same way it is for AgentCore entrypoints' warmed-up
    clients (design.md 12.6): the client object itself holds no per-request
    or expiring state, only endpoint/credential resolution."""
    global _ssm_client
    if _ssm_client is None:
        import boto3

        _ssm_client = boto3.client("ssm")
    return _ssm_client


def _get_from_ssm(name: str) -> str | None:
    """Resolve a secret from AWS SSM Parameter Store SecureString (Task
    18.1). Requests decryption; never logs the returned value; the
    identity this runs under is scoped to ``ssm:GetParameter`` on exactly
    this parameter prefix (CDK, ``infrastructure/cdk/stacks/
    api_stack.py``), never a broader SSM/KMS grant.
    """
    prefix = os.environ.get(_SSM_PREFIX_ENV_VAR, "")
    client = _get_ssm_client()
    from botocore.exceptions import ClientError

    try:
        response = client.get_parameter(Name=f"{prefix}{name}", WithDecryption=True)
    except ClientError as error:
        error_code = error.response.get("Error", {}).get("Code")
        if error_code == "ParameterNotFound":
            return None
        raise
    value = response["Parameter"]["Value"]
    return str(value)


def get_secret(name: str, *, default: str | None = None) -> str:
    """Return the secret value for ``name``.

    Resolution depends on the ``SECRET_BACKEND`` environment variable:

    * ``"env"`` (default): read the environment variable named ``name``.
    * ``"ssm"``: read the value from AWS SSM Parameter Store SecureString.

    Args:
        name: The logical secret name, e.g. ``"TAVILY_API_KEY"``. This is also
            the environment-variable name under the ``env`` backend.
        default: Value returned when the secret is absent. If ``None`` (the
            default) and the secret is missing, a :class:`SecretNotFoundError`
            is raised instead.

    Returns:
        The resolved secret value.

    Raises:
        SecretNotFoundError: If the secret is not found and no ``default`` was
            supplied. The message contains only the secret *name*.

    Note:
        The returned value is sensitive. Callers must not log it, echo it, or
        persist it. Prefer passing it directly to the client that needs it.
    """
    backend = _resolve_backend()

    if backend == "env":
        value = _get_from_env(name)
    elif backend == "ssm":
        value = _get_from_ssm(name)
    else:
        raise ValueError(
            f"Unknown {_BACKEND_ENV_VAR}={backend!r}; expected 'env' or 'ssm'."
        )

    if value is None or value == "":
        if default is not None:
            return default
        raise SecretNotFoundError(
            f"Secret {name!r} is not set for backend {backend!r}."
        )
    return value


def get_tavily_api_key() -> str:
    """Convenience accessor for the Tavily API key.

    Resolves the ``TAVILY_API_KEY`` secret through :func:`get_secret`. The
    value is never logged or written to disk by this module.
    """
    return get_secret("TAVILY_API_KEY")
