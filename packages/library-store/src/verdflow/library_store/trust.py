"""Append-only user trust decisions for exact strategy package hashes."""

from __future__ import annotations

from verdflow.contracts import (
    StrategyTrustDecision,
    StrategyTrustState,
    require_package_hash,
)

from .database import LibraryDatabase
from .errors import StrategyNotInstalledError


class StrategyTrustRepository:
    def __init__(self, database: LibraryDatabase) -> None:
        self._database = database

    def decide(
        self,
        *,
        package_hash: str,
        state: StrategyTrustState,
        decided_at: str,
        decided_by: str,
        reason: str | None,
    ) -> StrategyTrustDecision:
        require_package_hash(package_hash)
        normalized_state = StrategyTrustState(state)
        actor = _require_text(decided_by, "decided_by")
        occurred = _require_text(decided_at, "decided_at")
        normalized_reason = reason.strip() if reason and reason.strip() else None
        with self._database.transaction() as connection:
            installed = connection.execute(
                "SELECT 1 FROM strategy_packages WHERE package_hash = ?",
                (package_hash,),
            ).fetchone()
            if installed is None:
                raise StrategyNotInstalledError(package_hash)
            current = connection.execute(
                """
                SELECT state, decided_at, decided_by, reason
                FROM strategy_trust_events
                WHERE package_hash = ?
                ORDER BY event_id DESC LIMIT 1
                """,
                (package_hash,),
            ).fetchone()
            desired = (
                normalized_state.value,
                occurred,
                actor,
                normalized_reason,
            )
            if current is None or tuple(current) != desired:
                connection.execute(
                    """
                    INSERT INTO strategy_trust_events(
                        package_hash, state, decided_at, decided_by, reason
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        package_hash,
                        normalized_state.value,
                        occurred,
                        actor,
                        normalized_reason,
                    ),
                )
        return StrategyTrustDecision(
            package_hash=package_hash,
            state=normalized_state,
            decided_at=occurred,
            decided_by=actor,
        )

    def get(self, package_hash: str) -> StrategyTrustDecision | None:
        require_package_hash(package_hash)
        row = self._database.connection.execute(
            """
            SELECT package_hash, state, decided_at, decided_by
            FROM strategy_trust_events
            WHERE package_hash = ?
            ORDER BY event_id DESC LIMIT 1
            """,
            (package_hash,),
        ).fetchone()
        if row is None:
            return None
        return StrategyTrustDecision(
            package_hash=str(row["package_hash"]),
            state=StrategyTrustState(str(row["state"])),
            decided_at=str(row["decided_at"]),
            decided_by=str(row["decided_by"]),
        )

    def event_count(self, package_hash: str) -> int:
        require_package_hash(package_hash)
        row = self._database.connection.execute(
            "SELECT COUNT(*) FROM strategy_trust_events WHERE package_hash = ?",
            (package_hash,),
        ).fetchone()
        return int(row[0])


def _require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()
