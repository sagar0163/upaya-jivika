"""Regression test: every env var this app actually reads must be declared
in render.yaml's envVars list.

An undeclared env var isn't a crash on Render — it's just silently None,
which for something like API_AUTH_TOKEN means every state-mutating endpoint
fails closed (503) with no error pointing at the actual cause. This was
discovered via a manual audit (API_AUTH_TOKEN, the brain_router LLM fallback
keys, GITHUB_TOKEN, the Payoneer/email-IMAP keys were all missing from
render.yaml despite being read in code) — this test locks that fix in place.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Every env var read via os.environ.get(...)/os.getenv(...) with a literal
# name in src/*.py or main.py, plus the LLM-provider keys in brain_router.py
# (passed as an `api_key_env=` config field and looked up dynamically, so a
# static grep for os.environ.get("...") can't find them).
_REQUIRED_ENV_VARS = {
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "PAYONEER_WEBHOOK_SECRET",
    "NVIDIA_API_KEY",
    "HF_TOKEN",
    "API_AUTH_TOKEN",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY",
    "GITHUB_TOKEN",
    "PAYONEER_API_KEY",
    "PAYONEER_PROGRAM_ID",
    "EMAIL_IMAP_HOST",
    "EMAIL_IMAP_PORT",
    "EMAIL_IMAP_USER",
    "EMAIL_IMAP_PASSWORD",
    "TWOCAPTCHA_API_KEY",
    "VAULT_ENCRYPTION_KEY",
}


def _declared_render_env_vars() -> set[str]:
    config = yaml.safe_load((_REPO_ROOT / "render.yaml").read_text())
    env_vars = config["services"][0]["envVars"]
    return {entry["key"] for entry in env_vars}


def test_render_yaml_is_valid_yaml():
    assert (_REPO_ROOT / "render.yaml").exists()
    _declared_render_env_vars()  # raises on parse failure


def test_all_required_env_vars_declared_in_render_yaml():
    declared = _declared_render_env_vars()
    missing = _REQUIRED_ENV_VARS - declared
    assert not missing, (
        f"These env vars are read in code but not declared in render.yaml, "
        f"so they'll be silently None in production: {sorted(missing)}"
    )
