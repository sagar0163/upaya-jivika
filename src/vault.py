"""Credentials Vault — centralised secrets access (artifact.md §13 item).

Two backends, chosen at startup:

* **EnvSecretStore** — reads API keys from environment variables.  Already the
  de-facto pattern (brain_router reads os.getenv directly); this wraps it in a
  uniform interface so callers don't need to know *where* a secret lives.

* **SupabaseSecretStore** — reads/writes platform passwords in a Supabase table
  ``credentials``.  Used for runtime-mutable secrets (Clickworker password, etc.)
  that can't live in Render env vars because they may rotate.

* **CredentialsVault** — facade that tries Supabase first, falls back to env.
  Consistent with the persistence layer's fallback pattern.

Design principle: API keys stay in env vars (infrastructure concern).  Platform
passwords go to Supabase (runtime concern).  The vault unifies access.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

_ENCRYPTED_PREFIX = "enc:v1:"


def _get_fernet():
    """Return a Fernet cipher derived from ``VAULT_ENCRYPTION_KEY``, or None.

    Soft dependency, same pattern as the rest of this codebase's optional
    secrets: if the env var isn't set, callers fall back to storing values
    in plaintext (with a loud warning) rather than crashing — but a Supabase
    read-access leak (leaked service key, RLS misconfiguration, or a DB
    dump) would then expose every platform login password in cleartext.
    Setting this var is what actually closes that hole.
    """
    key = os.environ.get("VAULT_ENCRYPTION_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet

    derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
    return Fernet(derived)


# ---------------------------------------------------------------------------
# Abstract store
# ---------------------------------------------------------------------------

class SecretStore(ABC):
    """Interface for secret retrieval / storage."""

    @abstractmethod
    def get(self, provider: str, key: str = "password") -> str | None:
        """Return a secret, or ``None`` if not found."""
        ...

    @abstractmethod
    def set(self, provider: str, value: str, key: str = "password") -> None:
        """Store (or overwrite) a secret."""
        ...

    @abstractmethod
    def list_providers(self) -> list[str]:
        """Return provider names that have stored secrets."""
        ...

    @abstractmethod
    def delete(self, provider: str, key: str = "password") -> bool:
        """Delete a specific secret.  Returns True if it existed."""
        ...


# ---------------------------------------------------------------------------
# Env-var store (API keys — static, never rotated at runtime)
# ---------------------------------------------------------------------------

class EnvSecretStore(SecretStore):
    """Reads secrets from environment variables.

    Convention: the env var name is ``<PROVIDER>_API_KEY`` or
    ``<PROVIDER>_PASSWORD`` depending on the key type.  For API keys
    ``brain_router.py`` already reads env vars directly — this store
    exists so the *vault* interface works uniformly.
    """

    def __init__(self, env_prefix: str = "") -> None:
        self._prefix = env_prefix

    def _env_name(self, provider: str, key: str) -> str:
        """Build env-var name: ``PREFIX_PROVIDER_KEY``."""
        parts = [p for p in (self._prefix, provider, key) if p]
        return "_".join(parts).upper().replace("-", "_")

    def get(self, provider: str, key: str = "password") -> str | None:
        env_name = self._env_name(provider, key)
        return os.environ.get(env_name)

    def set(self, provider: str, value: str, key: str = "password") -> None:
        env_name = self._env_name(provider, key)
        os.environ[env_name] = value

    def list_providers(self) -> list[str]:
        providers: set[str] = set()
        prefix_upper = self._prefix.upper() if self._prefix else ""
        for env_name in os.environ:
            parts = env_name.split("_")
            if len(parts) >= 2:
                if prefix_upper and parts[0] == prefix_upper:
                    candidate = parts[1]
                elif not prefix_upper:
                    candidate = parts[0]
                else:
                    continue
                # Only include if it looks like a credential env var
                suffix = parts[-1].upper()
                if suffix in ("KEY", "PASSWORD", "SECRET", "TOKEN"):
                    providers.add(candidate.lower())
        return sorted(providers)

    def delete(self, provider: str, key: str = "password") -> bool:
        env_name = self._env_name(provider, key)
        if env_name in os.environ:
            del os.environ[env_name]
            return True
        return False


# ---------------------------------------------------------------------------
# Supabase store (platform passwords — runtime-mutable)
# ---------------------------------------------------------------------------

class SupabaseSecretStore(SecretStore):
    """Reads/writes secrets in a ``credentials`` Supabase table.

    Table schema (created automatically on first write)::

        CREATE TABLE IF NOT EXISTS credentials (
            provider TEXT NOT NULL,
            key      TEXT NOT NULL DEFAULT 'password',
            value    TEXT NOT NULL,
            updated  TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (provider, key)
        );
    """

    def __init__(self) -> None:
        from supabase import create_client
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        self._client = create_client(url, key)
        self._ensure_table()

    def _ensure_table(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS credentials (
            provider TEXT NOT NULL,
            key      TEXT NOT NULL DEFAULT 'password',
            value    TEXT NOT NULL,
            updated  TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (provider, key)
        );
        """
        try:
            self._client.rpc("exec_sql", {"query": ddl}).execute()
        except Exception:  # noqa: BLE001 - intentional graceful fallback
            logger.debug("credentials table bootstrap skipped (RPC unavailable)")

    def get(self, provider: str, key: str = "password") -> str | None:
        resp = (
            self._client.table("credentials")
            .select("value")
            .eq("provider", provider)
            .eq("key", key)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        raw = rows[0]["value"]
        if not raw.startswith(_ENCRYPTED_PREFIX):
            # Legacy plaintext row written before VAULT_ENCRYPTION_KEY was
            # configured — return as-is rather than failing the lookup.
            return raw
        fernet = _get_fernet()
        if fernet is None:
            logger.error(
                f"Credential {provider}/{key} is encrypted but VAULT_ENCRYPTION_KEY "
                "is not set — cannot decrypt"
            )
            return None
        token = raw[len(_ENCRYPTED_PREFIX):]
        return fernet.decrypt(token.encode()).decode()

    def set(self, provider: str, value: str, key: str = "password") -> None:
        fernet = _get_fernet()
        if fernet is not None:
            stored_value = _ENCRYPTED_PREFIX + fernet.encrypt(value.encode()).decode()
        else:
            logger.warning(
                f"Storing credential {provider}/{key} in plaintext — "
                "set VAULT_ENCRYPTION_KEY to encrypt platform passwords at rest"
            )
            stored_value = value
        self._client.table("credentials").upsert(
            {"provider": provider, "key": key, "value": stored_value},
            on_conflict="provider,key",
        ).execute()
        logger.info(f"Stored credential for {provider}/{key}")

    def list_providers(self) -> list[str]:
        resp = (
            self._client.table("credentials")
            .select("provider")
            .execute()
        )
        return sorted({r["provider"] for r in (resp.data or [])})

    def delete(self, provider: str, key: str = "password") -> bool:
        resp = (
            self._client.table("credentials")
            .delete()
            .eq("provider", provider)
            .eq("key", key)
            .execute()
        )
        return bool(resp.data)


# ---------------------------------------------------------------------------
# In-memory store (for tests)
# ---------------------------------------------------------------------------

class InMemorySecretStore(SecretStore):
    """Dict-backed secret store for local dev and testing."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get(self, provider: str, key: str = "password") -> str | None:
        return self._store.get((provider, key))

    def set(self, provider: str, value: str, key: str = "password") -> None:
        self._store[(provider, key)] = value

    def list_providers(self) -> list[str]:
        return sorted({p for p, _ in self._store})

    def delete(self, provider: str, key: str = "password") -> bool:
        return self._store.pop((provider, key), None) is not None


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

class CredentialsVault:
    """Unified credentials access.

    Lookup order:
    1. Explicit overrides (``set_override``) — for runtime-injected creds.
    2. Supabase secrets (when available).
    3. Environment variables.
    """

    def __init__(
        self,
        supabase_store: SecretStore | None = None,
        env_store: EnvSecretStore | None = None,
    ) -> None:
        self._overrides: dict[tuple[str, str], str] = {}
        self._env = env_store or EnvSecretStore()
        # Lazy-init Supabase — may not be available in tests
        self._supabase: SecretStore | None = supabase_store

    def _get_supabase(self) -> SecretStore | None:
        """Lazy-init Supabase store if env vars present."""
        if self._supabase is not None:
            return self._supabase
        if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"):
            try:
                self._supabase = SupabaseSecretStore()
                logger.info("Vault: Supabase secret store initialised")
            except Exception as e:  # noqa: BLE001 - intentional fallback
                logger.warning(f"Vault: Supabase init failed: {e}")
                self._supabase = False  # type: ignore[assignment]
        return self._supabase if self._supabase else None

    # -- public API -----------------------------------------------------------

    def get(
        self, provider: str, key: str = "password", *, env_key: str | None = None
    ) -> str | None:
        """Retrieve a secret.  Checks overrides → Supabase → env vars."""
        # 1. Overrides
        if (provider, key) in self._overrides:
            return self._overrides[(provider, key)]

        # 2. Supabase
        sb = self._get_supabase()
        if sb is not None:
            val = sb.get(provider, key)
            if val is not None:
                return val

        # 3. Env vars — try the explicit env_key first, then fallback pattern
        if env_key:
            val = os.environ.get(env_key)
            if val is not None:
                return val
        return self._env.get(provider, key)

    def get_api_key(self, provider: str) -> str | None:
        """Convenience: retrieve an API key for an AI provider."""
        return self.get(provider, key="api_key")

    def get_password(self, provider: str) -> str | None:
        """Convenience: retrieve a platform login password."""
        return self.get(provider, key="password")

    def set(self, provider: str, value: str, key: str = "password") -> None:
        """Store a secret in Supabase (requires Supabase availability)."""
        sb = self._get_supabase()
        if sb is None:
            logger.warning(
                f"Vault: cannot persist {provider}/{key} — "
                "Supabase unavailable; storing as override only"
            )
            self._overrides[(provider, key)] = value
            return
        sb.set(provider, value, key)

    def set_override(self, provider: str, value: str, key: str = "password") -> None:
        """Set an in-memory override (takes precedence over everything)."""
        self._overrides[(provider, key)] = value

    def clear_override(self, provider: str, key: str = "password") -> bool:
        """Remove an override.  Returns True if one existed."""
        return self._overrides.pop((provider, key), None) is not None

    def list_providers(self) -> list[str]:
        """Union of providers across all stores."""
        providers: set[str] = set()
        sb = self._get_supabase()
        if sb is not None:
            providers.update(sb.list_providers())
        providers.update(self._env.list_providers())
        for p, _ in self._overrides:
            providers.add(p)
        return sorted(providers)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_vault: CredentialsVault | None = None


def get_vault() -> CredentialsVault:
    """Get (or create) the global vault singleton."""
    global _vault
    if _vault is None:
        _vault = CredentialsVault()
    return _vault


def create_vault(**kwargs) -> CredentialsVault:
    """Create a fresh vault instance (useful in tests)."""
    return CredentialsVault(**kwargs)
