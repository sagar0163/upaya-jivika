#!/usr/bin/env python3
"""Encrypt legacy plaintext rows in the Supabase ``credentials`` table.

Migrates any row written before ``VAULT_ENCRYPTION_KEY`` was configured
(issue #66) so the vault holds no plaintext passwords at rest. Rows that
already carry the ``enc:v1:`` prefix are left untouched; lookups return the
same values before and after (``vault.get()`` decrypts under the prefix).

Requirements:
    * ``SUPABASE_URL`` / ``SUPABASE_KEY`` — the credentials table to migrate.
    * ``VAULT_ENCRYPTION_KEY`` — hard-required; the script refuses to run
      without it so it can never scatter new ciphertext under a throwaway key.

Usage::

    python scripts/migrate_plaintext_vault.py [--dry-run]

``--dry-run`` only reports what *would* be encrypted without writing anything.
"""

from __future__ import annotations

import argparse
import os
import sys

# Add project root to path so `src` is importable standalone.
sys.path.insert(0, ".")

from supabase import create_client

from src import vault


def migrate_plaintext_rows(client, fernet, table_name: str = "credentials", *, dry_run: bool = False) -> dict:
    """Encrypt every non-prefixed row in ``credentials``.

    Returns a counts dict: ``{"total", "already_encrypted", "migrated"}``.
    With ``dry_run=True`` nothing is written — only counted.
    """
    resp = client.table(table_name).select("provider,key,value").execute()
    rows = resp.data or []

    already_encrypted = 0
    migrated = 0
    for row in rows:
        value = row.get("value") or ""
        if value.startswith(vault._ENCRYPTED_PREFIX):
            already_encrypted += 1
            continue

        encrypted = vault._ENCRYPTED_PREFIX + fernet.encrypt(value.encode()).decode()
        if not dry_run:
            client.table(table_name).upsert(
                {
                    "provider": row["provider"],
                    "key": row.get("key", "password"),
                    "value": encrypted,
                },
                on_conflict="provider,key",
            ).execute()
        migrated += 1

    return {
        "total": len(rows),
        "already_encrypted": already_encrypted,
        "migrated": migrated,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Encrypt legacy plaintext rows in the Supabase credentials table."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be migrated without writing anything.",
    )
    args = parser.parse_args(argv)

    fernet = vault._get_fernet()
    if fernet is None:
        print(
            "ERROR: VAULT_ENCRYPTION_KEY must be set to run the migration — "
            "the script refuses to encrypt rows under a throwaway key.",
            file=sys.stderr,
        )
        return 1

    url = os.environ.get("SUPABASE_URL") or ""
    key = os.environ.get("SUPABASE_KEY") or ""
    if not url or not key:
        print(
            "ERROR: SUPABASE_URL and SUPABASE_KEY are required.",
            file=sys.stderr,
        )
        return 1

    client = create_client(url, key)
    counts = migrate_plaintext_rows(client, fernet, dry_run=args.dry_run)

    verb = "Would migrate" if args.dry_run else "Migrated"
    print(
        f"{verb} {counts['migrated']} plaintext row(s); "
        f"{counts['already_encrypted']} already encrypted; "
        f"{counts['total']} total."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())