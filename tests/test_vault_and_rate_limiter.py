"""Unit tests for vault.py and rate_limiter.py.

All Supabase calls are avoided — tests use in-memory stores only.
"""

import os

import pytest

# ============================================================================
# vault.py — InMemorySecretStore
# ============================================================================

class TestInMemorySecretStore:
    """Test the in-memory secret store."""

    def test_set_and_get(self):
        from src.vault import InMemorySecretStore
        store = InMemorySecretStore()
        store.set("clickworker", "pass123")
        assert store.get("clickworker") == "pass123"

    def test_get_returns_none_for_missing(self):
        from src.vault import InMemorySecretStore
        store = InMemorySecretStore()
        assert store.get("nonexistent") is None

    def test_custom_key(self):
        from src.vault import InMemorySecretStore
        store = InMemorySecretStore()
        store.set("clickworker", "user@email.com", key="email")
        assert store.get("clickworker", key="email") == "user@email.com"
        assert store.get("clickworker", key="password") is None

    def test_list_providers(self):
        from src.vault import InMemorySecretStore
        store = InMemorySecretStore()
        store.set("clickworker", "pw1")
        store.set("toloka", "pw2")
        store.set("clickworker", "e@x.com", key="email")
        providers = store.list_providers()
        assert "clickworker" in providers
        assert "toloka" in providers

    def test_delete_existing(self):
        from src.vault import InMemorySecretStore
        store = InMemorySecretStore()
        store.set("clickworker", "pw")
        assert store.delete("clickworker") is True
        assert store.get("clickworker") is None

    def test_delete_nonexistent(self):
        from src.vault import InMemorySecretStore
        store = InMemorySecretStore()
        assert store.delete("nope") is False

    def test_overwrite(self):
        from src.vault import InMemorySecretStore
        store = InMemorySecretStore()
        store.set("clickworker", "old")
        store.set("clickworker", "new")
        assert store.get("clickworker") == "new"


# ============================================================================
# vault.py — EnvSecretStore
# ============================================================================

class TestEnvSecretStore:
    """Test the env-var secret store."""

    def test_get_existing(self):
        from src.vault import EnvSecretStore
        os.environ["TEST_VAULT_KEY"] = "secret-value"
        try:
            store = EnvSecretStore()
            # Convention: provider=test_vault, key=secret => TEST_VAULT_SECRET
            # But let's test the raw env fallback
            env_name = store._env_name("test_provider", "api_key")
            os.environ[env_name] = "abc123"
            assert store.get("test_provider", "api_key") == "abc123"
        finally:
            os.environ.pop("TEST_VAULT_KEY", None)
            os.environ.pop(env_name, None)

    def test_get_missing_returns_none(self):
        from src.vault import EnvSecretStore
        store = EnvSecretStore()
        assert store.get("nonexistent", "password") is None

    def test_set_creates_env_var(self):
        from src.vault import EnvSecretStore
        store = EnvSecretStore()
        store.set("test_p", "val", key="token")
        env_name = store._env_name("test_p", "token")
        assert os.environ.get(env_name) == "val"
        # Cleanup
        del os.environ[env_name]

    def test_delete(self):
        from src.vault import EnvSecretStore
        store = EnvSecretStore()
        store.set("del_test", "x", key="token")
        env_name = store._env_name("del_test", "token")
        assert store.delete("del_test", "token") is True
        assert os.environ.get(env_name) is None
        assert store.delete("del_test", "token") is False


# ============================================================================
# vault.py — CredentialsVault facade
# ============================================================================

class TestCredentialsVault:
    """Test the vault facade with in-memory backend."""

    def _make_vault(self):
        from src.vault import CredentialsVault
        # Use two InMemory stores: one pretending to be "supabase", one env
        # We can't actually test Supabase here, but we can test the override
        # and env fallback paths.
        vault = CredentialsVault(supabase_store=None, env_store=None)
        return vault

    def test_set_override_takes_precedence(self):
        vault = self._make_vault()
        vault.set_override("clickworker", "override-pw")
        assert vault.get("clickworker") == "override-pw"

    def test_clear_override(self):
        vault = self._make_vault()
        vault.set_override("clickworker", "pw")
        assert vault.clear_override("clickworker") is True
        assert vault.get("clickworker") is None
        assert vault.clear_override("clickworker") is False

    def test_env_fallback(self):
        from src.vault import EnvSecretStore
        os.environ["VAULTTEST_PASSWORD"] = "env-pw"
        try:
            vault = self._make_vault()
            vault._env = EnvSecretStore()
            result = vault.get("vaulttest", "password")
            assert result == "env-pw"
        finally:
            del os.environ["VAULTTEST_PASSWORD"]

    def test_get_api_key_convenience(self):
        vault = self._make_vault()
        vault.set_override("nvidia", "nvidia-key", key="api_key")
        assert vault.get_api_key("nvidia") == "nvidia-key"

    def test_get_password_convenience(self):
        vault = self._make_vault()
        vault.set_override("clickworker", "cw-pw", key="password")
        assert vault.get_password("clickworker") == "cw-pw"

    def test_list_providers_merges_overrides_and_env(self):
        from src.vault import EnvSecretStore
        os.environ["MERGETEST_KEY"] = "val"
        try:
            vault = self._make_vault()
            vault._env = EnvSecretStore()
            vault.set_override("override_only", "x", key="password")
            providers = vault.list_providers()
            assert "mergetest" in providers
            assert "override_only" in providers
        finally:
            del os.environ["MERGETEST_KEY"]

    def test_create_vault_factory(self):
        from src.vault import create_vault
        vault = create_vault()
        vault.set_override("test", "val")
        assert vault.get("test") == "val"


# ============================================================================
# rate_limiter.py — InMemoryRateLimitStore
# ============================================================================

class TestInMemoryRateLimitStore:
    """Test the in-memory rate-limit store."""

    def test_increment_and_get(self):
        from src.rate_limiter import InMemoryRateLimitStore
        store = InMemoryRateLimitStore()
        assert store.increment("nvidia_nim", "minute") == 1
        assert store.increment("nvidia_nim", "minute") == 2
        assert store.get_count("nvidia_nim", "minute") == 2

    def test_reset_window(self):
        from src.rate_limiter import InMemoryRateLimitStore
        store = InMemoryRateLimitStore()
        store.increment("groq", "minute")
        store.increment("groq", "minute")
        store.reset_window("groq", "minute")
        assert store.get_count("groq", "minute") == 0
        # Other window unaffected
        store.increment("groq", "day")
        assert store.get_count("groq", "day") == 1

    def test_separate_providers(self):
        from src.rate_limiter import InMemoryRateLimitStore
        store = InMemoryRateLimitStore()
        store.increment("nvidia_nim", "minute")
        store.increment("groq", "minute")
        assert store.get_count("nvidia_nim", "minute") == 1
        assert store.get_count("groq", "minute") == 1

    def test_save_and_load_state(self):
        from src.rate_limiter import InMemoryRateLimitStore
        store = InMemoryRateLimitStore()
        store.increment("nvidia_nim", "minute")
        store.increment("nvidia_nim", "day")
        store.increment("groq", "minute")
        state = store.load_state()
        assert state["nvidia_nim"]["minute"] == 1
        assert state["nvidia_nim"]["day"] == 1
        assert state["groq"]["minute"] == 1

    def test_get_count_missing_returns_zero(self):
        from src.rate_limiter import InMemoryRateLimitStore
        store = InMemoryRateLimitStore()
        assert store.get_count("nonexistent", "minute") == 0


# ============================================================================
# rate_limiter.py — RateLimitTracker
# ============================================================================

class TestRateLimitTracker:
    """Test the rate-limit tracker facade."""

    def test_all_providers_available_initially(self):
        from src.rate_limiter import InMemoryRateLimitStore, RateLimitTracker
        tracker = RateLimitTracker(InMemoryRateLimitStore())
        from src.rate_limiter import PROVIDER_LIMITS
        for name in PROVIDER_LIMITS:
            assert tracker.is_available(name) is True

    def test_record_usage_increments_counter(self):
        from src.rate_limiter import InMemoryRateLimitStore, RateLimitTracker
        tracker = RateLimitTracker(InMemoryRateLimitStore())
        tracker.record_usage("nvidia_nim")
        usage = tracker.get_usage("nvidia_nim")
        assert usage["minute"] == 1

    def test_provider_becomes_unavailable_at_limit(self):
        from src.rate_limiter import InMemoryRateLimitStore, RateLimitTracker
        # Mistral has rpm=2, so with safety_margin=0.85 the safe budget is 1
        tracker = RateLimitTracker(
            InMemoryRateLimitStore(), safety_margin=0.85
        )
        assert tracker.is_available("mistral") is True
        tracker.record_usage("mistral")  # count=1, budget=int(2*0.85)=1
        assert tracker.is_available("mistral") is False

    def test_provider_stays_available_below_limit(self):
        from src.rate_limiter import InMemoryRateLimitStore, RateLimitTracker
        # NVIDIA_NIM has rpm=40, safe budget = 34
        tracker = RateLimitTracker(InMemoryRateLimitStore())
        for _ in range(33):
            tracker.record_usage("nvidia_nim")
        assert tracker.is_available("nvidia_nim") is True

    def test_unknown_provider_always_available(self):
        from src.rate_limiter import InMemoryRateLimitStore, RateLimitTracker
        tracker = RateLimitTracker(InMemoryRateLimitStore())
        assert tracker.is_available("unknown_provider") is True

    def test_get_usage_unknown_provider(self):
        from src.rate_limiter import InMemoryRateLimitStore, RateLimitTracker
        tracker = RateLimitTracker(InMemoryRateLimitStore())
        usage = tracker.get_usage("nonexistent")
        assert usage == {"minute": 0, "day": 0}

    def test_get_limits(self):
        from src.rate_limiter import PROVIDER_LIMITS, InMemoryRateLimitStore, RateLimitTracker
        tracker = RateLimitTracker(InMemoryRateLimitStore())
        limits = tracker.get_limits("nvidia_nim")
        assert limits == PROVIDER_LIMITS["nvidia_nim"]
        assert tracker.get_limits("nonexistent") is None

    def test_get_status(self):
        from src.rate_limiter import PROVIDER_LIMITS, InMemoryRateLimitStore, RateLimitTracker
        tracker = RateLimitTracker(InMemoryRateLimitStore())
        status = tracker.get_status()
        assert len(status) == len(PROVIDER_LIMITS)
        for name in PROVIDER_LIMITS:
            assert name in status
            assert status[name]["available"] is True
            assert status[name]["usage_minute"] == 0

    def test_reset_provider(self):
        from src.rate_limiter import InMemoryRateLimitStore, RateLimitTracker
        tracker = RateLimitTracker(InMemoryRateLimitStore())
        tracker.record_usage("groq")
        tracker.record_usage("groq")
        tracker.reset_provider("groq")
        assert tracker.get_usage("groq") == {"minute": 0, "day": 0}
        assert tracker.is_available("groq") is True

    def test_reset_all(self):
        from src.rate_limiter import InMemoryRateLimitStore, RateLimitTracker
        tracker = RateLimitTracker(InMemoryRateLimitStore())
        tracker.record_usage("nvidia_nim")
        tracker.record_usage("groq")
        tracker.reset_all()
        assert tracker.get_usage("nvidia_nim") == {"minute": 0, "day": 0}
        assert tracker.get_usage("groq") == {"minute": 0, "day": 0}

    def test_safety_margin_custom(self):
        from src.rate_limiter import InMemoryRateLimitStore, RateLimitTracker
        # With safety_margin=1.0 (no margin), budget equals limit
        tracker = RateLimitTracker(InMemoryRateLimitStore(), safety_margin=1.0)
        # Mistral rpm=2: count 1 should still be available with 100% margin
        tracker.record_usage("mistral")
        assert tracker.is_available("mistral") is True
        tracker.record_usage("mistral")  # count=2, budget=2
        assert tracker.is_available("mistral") is False

    def test_checkpoint_and_restore(self):
        from src.rate_limiter import InMemoryRateLimitStore, RateLimitTracker
        store = InMemoryRateLimitStore()
        tracker = RateLimitTracker(store)
        tracker.record_usage("nvidia_nim")
        tracker.record_usage("nvidia_nim")
        tracker.checkpoint()
        # Create new tracker with same store — state should be preserved
        tracker2 = RateLimitTracker(store)
        usage = tracker2.get_usage("nvidia_nim")
        assert usage["minute"] == 2

    def test_rpd_limit_enforced(self):
        from src.rate_limiter import InMemoryRateLimitStore, RateLimitTracker
        # Groq: rpd=14400, safe budget=int(14400*0.85)=12240
        # We won't fill that in a test, but verify the logic path works
        tracker = RateLimitTracker(InMemoryRateLimitStore())
        # Manually set the day counter via the store
        tracker._store.increment("groq", "day")
        # Should still be available
        assert tracker.is_available("groq") is True


# ============================================================================
# brain_router.py — BrainRouter + RateLimitTracker integration
# ============================================================================

class TestBrainRouterRateLimitIntegration:
    """Test that BrainRouter skips providers at rate limit."""

    @pytest.mark.asyncio
    async def test_skips_rate_limited_provider(self):
        """BrainRouter.complete() should skip a provider whose RPM limit is reached."""
        from src.brain_router import BrainRouter, CompletionRequest, Provider, TaskType
        from src.rate_limiter import InMemoryRateLimitStore, RateLimitTracker

        # Mistral: rpm=2, safe budget with 0.85 margin = 1
        os.environ.setdefault("MISTRAL_API_KEY", "test-mistral-key")
        limiter = RateLimitTracker(InMemoryRateLimitStore(), safety_margin=0.85)
        limiter.record_usage("mistral")  # now at budget — should be skipped

        router = BrainRouter(rate_limiter=limiter)
        router._initialize_clients()

        if Provider.MISTRAL not in router.clients:
            pytest.skip("MISTRAL client not initialised (no API key)")

        original_complete = router.clients[Provider.MISTRAL].complete
        called = []

        async def mock_complete(req):
            called.append(True)
            return await original_complete(req)

        router.clients[Provider.MISTRAL].complete = mock_complete

        request = CompletionRequest(
            prompt="test",
            task_type=TaskType.GENERAL,
        )
        await router.complete(request)
        assert len(called) == 0  # Mistral was skipped


# ============================================================================
# vault.py — SupabaseSecretStore (skipped without env vars)
# ============================================================================

class TestSupabaseSecretStoreEncryptionAtRest:
    """Mock-backed tests for the vault's encryption-at-rest fix.

    Bypasses SupabaseSecretStore.__init__ (which needs real Supabase env
    vars) and injects a MagicMock client instead, so these run without any
    external service.
    """

    def _store_with_mock_client(self, monkeypatch, mock_client, *, encryption_key="s3cr3t-key"):
        from src.vault import SupabaseSecretStore

        if encryption_key is not None:
            monkeypatch.setenv("VAULT_ENCRYPTION_KEY", encryption_key)
        else:
            monkeypatch.delenv("VAULT_ENCRYPTION_KEY", raising=False)

        store = SupabaseSecretStore.__new__(SupabaseSecretStore)
        store._client = mock_client
        return store

    @staticmethod
    def _set_select_result(mock_client, rows):
        chain = mock_client.table.return_value.select.return_value.eq.return_value.eq
        chain.return_value.limit.return_value.execute.return_value.data = rows

    def test_set_encrypts_value_before_storing(self, monkeypatch):
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        store = self._store_with_mock_client(monkeypatch, mock_client)

        store.set("clickworker", "my-real-password")

        upserted = mock_client.table.return_value.upsert.call_args[0][0]
        assert upserted["value"] != "my-real-password"
        assert upserted["value"].startswith("enc:v1:")

    def test_get_decrypts_a_value_it_encrypted(self, monkeypatch):
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        store = self._store_with_mock_client(monkeypatch, mock_client)

        store.set("clickworker", "my-real-password")
        stored_value = mock_client.table.return_value.upsert.call_args[0][0]["value"]

        self._set_select_result(mock_client, [
            {"value": stored_value}
        ])

        assert store.get("clickworker") == "my-real-password"

    def test_legacy_plaintext_row_still_readable(self, monkeypatch):
        """A row written before VAULT_ENCRYPTION_KEY existed must not break —
        it's returned as-is rather than failing the lookup."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        store = self._store_with_mock_client(monkeypatch, mock_client)

        self._set_select_result(mock_client, [
            {"value": "old-plaintext-password"}
        ])

        assert store.get("clickworker") == "old-plaintext-password"

    def test_no_encryption_key_stores_plaintext_with_warning(self, monkeypatch, caplog):
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        store = self._store_with_mock_client(monkeypatch, mock_client, encryption_key=None)

        store.set("clickworker", "my-real-password")

        upserted = mock_client.table.return_value.upsert.call_args[0][0]
        assert upserted["value"] == "my-real-password"
        assert "plaintext" in caplog.text.lower()

    def test_encrypted_value_unreadable_without_key(self, monkeypatch):
        """If the key is lost/unset after data was encrypted, get() must
        fail closed (return None) rather than return ciphertext as if it
        were the real password."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        store = self._store_with_mock_client(monkeypatch, mock_client, encryption_key="key-a")
        store.set("clickworker", "my-real-password")
        stored_value = mock_client.table.return_value.upsert.call_args[0][0]["value"]

        self._set_select_result(mock_client, [
            {"value": stored_value}
        ])
        monkeypatch.delenv("VAULT_ENCRYPTION_KEY", raising=False)

        assert store.get("clickworker") is None


@pytest.mark.skipif(
    not os.environ.get("SUPABASE_URL"),
    reason="Requires Supabase credentials"
)
class TestSupabaseSecretStore:
    """Integration tests — only run with real Supabase env vars."""

    def test_set_and_get(self):
        from src.vault import SupabaseSecretStore
        store = SupabaseSecretStore()
        store.set("test_provider", "test_value")
        assert store.get("test_provider") == "test_value"
        # Cleanup
        store.delete("test_provider")

    def test_list_providers(self):
        from src.vault import SupabaseSecretStore
        store = SupabaseSecretStore()
        providers = store.list_providers()
        assert isinstance(providers, list)

    def test_delete(self):
        from src.vault import SupabaseSecretStore
        store = SupabaseSecretStore()
        store.set("del_test_p", "val")
        assert store.delete("del_test_p") is True
        assert store.get("del_test_p") is None


@pytest.mark.skipif(
    not os.environ.get("SUPABASE_URL"),
    reason="Requires Supabase credentials"
)
class TestSupabaseRateLimitStore:
    """Integration tests — only run with real Supabase env vars."""

    def test_increment_and_get(self):
        from src.rate_limiter import SupabaseRateLimitStore
        store = SupabaseRateLimitStore()
        count = store.increment("nvidia_nim", "minute")
        assert count >= 1
        # Cleanup
        store.reset_window("nvidia_nim", "minute")
