import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


_DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[1] / ".cache" / "entity_lookup.sqlite3"


@dataclass(frozen=True)
class CachedEntityLookup:
    cache_key: str
    status: str
    state: str
    evidence_level: str
    score: int
    max_score: int
    finding: str
    fix: str
    checked_at: datetime


def _cache_path() -> Path:
    configured = os.getenv("ENTITY_CACHE_PATH")
    return Path(configured) if configured else _DEFAULT_CACHE_PATH


def entity_cache_ttl() -> timedelta:
    days = int(os.getenv("ENTITY_CACHE_TTL_DAYS", "30"))
    return timedelta(days=days)


def _connection() -> sqlite3.Connection:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_lookup_cache (
            cache_key TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            state TEXT NOT NULL,
            evidence_level TEXT NOT NULL,
            score INTEGER NOT NULL,
            max_score INTEGER NOT NULL,
            finding TEXT NOT NULL,
            fix TEXT NOT NULL,
            checked_at TEXT NOT NULL
        )
        """
    )
    return connection


def get_cached_entity_lookup(cache_key: str) -> CachedEntityLookup | None:
    connection = _connection()
    try:
        row = connection.execute(
            """
            SELECT cache_key, status, state, evidence_level, score, max_score, finding, fix, checked_at
            FROM entity_lookup_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    checked_at = datetime.fromisoformat(row[8])
    if datetime.now(UTC) - checked_at >= entity_cache_ttl():
        return None

    return CachedEntityLookup(
        cache_key=row[0],
        status=row[1],
        state=row[2],
        evidence_level=row[3],
        score=row[4],
        max_score=row[5],
        finding=row[6],
        fix=row[7],
        checked_at=checked_at,
    )


def set_cached_entity_lookup(
    cache_key: str,
    status: str,
    state: str,
    evidence_level: str,
    score: int,
    max_score: int,
    finding: str,
    fix: str,
) -> None:
    checked_at = datetime.now(UTC).isoformat()
    connection = _connection()
    try:
        connection.execute(
            """
            INSERT INTO entity_lookup_cache (
                cache_key, status, state, evidence_level, score, max_score, finding, fix, checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                status = excluded.status,
                state = excluded.state,
                evidence_level = excluded.evidence_level,
                score = excluded.score,
                max_score = excluded.max_score,
                finding = excluded.finding,
                fix = excluded.fix,
                checked_at = excluded.checked_at
            """,
            (cache_key, status, state, evidence_level, score, max_score, finding, fix, checked_at),
        )
        connection.commit()
    finally:
        connection.close()
