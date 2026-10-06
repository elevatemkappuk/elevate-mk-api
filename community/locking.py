"""Database-backed serialization primitives for Community workflows."""

import hashlib

from django.conf import settings
from django.db import connection


def community_join_email_lock_key(email: str) -> int:
    """Return a stable PostgreSQL advisory-lock key for a canonical email."""
    digest = hashlib.sha256(email.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def acquire_community_join_email_lock(email: str) -> bool:
    """Serialize Community Join transactions for one canonical email.

    PostgreSQL transaction-scoped advisory locks are released automatically on
    commit or rollback. SQLite is retained as an explicit development/test
    fallback because it does not provide equivalent advisory-lock semantics.
    Production deployments are required to use PostgreSQL.
    """
    if connection.vendor != "postgresql":
        if settings.DEBUG:
            return False
        raise RuntimeError("Community Join email locking requires PostgreSQL.")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [community_join_email_lock_key(email)],
        )
    return True


def community_mobile_lock_key(mobile: str) -> int:
    """Return a stable PostgreSQL advisory-lock key for a canonical mobile."""
    digest = hashlib.sha256(mobile.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def acquire_community_mobile_lock(mobile: str) -> bool:
    """Serialize Community mobile ownership checks for one canonical number."""
    if connection.vendor != "postgresql":
        if settings.DEBUG:
            return False
        raise RuntimeError("Community mobile locking requires PostgreSQL.")

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [community_mobile_lock_key(mobile)])
    return True


def acquire_community_email_change_lock(email: str) -> bool:
    """Serialize authenticated email-change collision checks for one email."""
    if connection.vendor != "postgresql":
        if settings.DEBUG:
            return False
        raise RuntimeError("Community email-change locking requires PostgreSQL.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [community_join_email_lock_key(email)])
    return True
