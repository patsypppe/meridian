"""Credentials: where they are allowed to be, and where they are not.

The rule is one sentence. A provider credential may exist in the harness process
and in the proxy container, and nowhere else. It never reaches a trial container,
a task file, a manifest, a log line, or a cassette.

That is not a policy applied at each call site; it is a property of the topology.
The trial container's environment is constructed empty in `isolation.py`, so
there is no code path that could put a key there by accident.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

PROVIDER_ENV_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")

# Anything matching one of these prefixes is redacted before it can be printed.
SECRET_PREFIXES = ("sk-ant-", "sk-", "gho_", "ghp_")

REDACTION = "«redacted»"


class MissingCredentialError(RuntimeError):
    """No provider credential is available, and this run needs one."""

    def __init__(self) -> None:
        super().__init__(
            "no provider credential found. Recording a cassette needs one of "
            f"{', '.join(PROVIDER_ENV_VARS)} — but replay does not, which is why CI "
            "runs offline and never needs a key at all."
        )


@dataclass(frozen=True)
class ProviderCredential:
    """A credential plus the header name the provider expects it in."""

    env_var: str
    value: str

    @property
    def header_name(self) -> str:
        return "x-api-key" if self.env_var == "ANTHROPIC_API_KEY" else "authorization"

    @property
    def header_value(self) -> str:
        return self.value if self.env_var == "ANTHROPIC_API_KEY" else f"Bearer {self.value}"

    def __repr__(self) -> str:  # pragma: no cover - defensive against accidental logging
        return f"ProviderCredential(env_var={self.env_var!r}, value={REDACTION})"

    __str__ = __repr__


def load_credential(env: dict[str, str] | None = None) -> ProviderCredential:
    """Read a credential from the environment, or explain why there isn't one."""
    environment = os.environ if env is None else env
    for name in PROVIDER_ENV_VARS:
        value = environment.get(name, "").strip()
        if value:
            return ProviderCredential(env_var=name, value=value)
    raise MissingCredentialError()


def has_credential(env: dict[str, str] | None = None) -> bool:
    environment = os.environ if env is None else env
    return any(environment.get(name, "").strip() for name in PROVIDER_ENV_VARS)


def redact(text: str) -> str:
    """Replace anything that looks like a credential.

    Applied to everything that leaves the harness as text — log lines, PR
    comments, failure details. A secret is only secret until it is printed once.
    """
    redacted = text
    for token in _secret_tokens(text):
        redacted = redacted.replace(token, REDACTION)
    return redacted


def _secret_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for candidate in text.replace('"', " ").replace("'", " ").split():
        stripped = candidate.strip(",;)")
        if any(stripped.startswith(prefix) for prefix in SECRET_PREFIXES) and len(stripped) > 12:
            tokens.append(stripped)
    return tokens


def assert_absent_from(environment: dict[str, str] | list[str], *, context: str) -> None:
    """Fail loudly if a credential appears where it must not.

    Used against a trial container's inspected environment. A silent leak is the
    kind of thing you discover from someone else's bill.
    """
    entries = (
        environment
        if isinstance(environment, list)
        else [f"{k}={v}" for k, v in environment.items()]
    )
    for entry in entries:
        name = entry.split("=", 1)[0]
        if name in PROVIDER_ENV_VARS:
            raise AssertionError(f"{context} exposes {name}; credentials never enter a trial")
        value = entry.split("=", 1)[1] if "=" in entry else ""
        if _secret_tokens(value):
            raise AssertionError(f"{context} exposes a credential-shaped value in {name}")
