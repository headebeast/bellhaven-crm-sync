"""SQLite persistence providing idempotent re-runs and audit history."""

from __future__ import annotations

from contextlib import closing, contextmanager
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import Proposal

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS proposals (
    fingerprint TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    facility_name TEXT NOT NULL,
    target_account_id TEXT NOT NULL,
    recommended_decision TEXT NOT NULL,
    confidence TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    operations_json TEXT NOT NULL,
    decision_status TEXT NOT NULL DEFAULT 'pending',
    decision_note TEXT NOT NULL DEFAULT '',
    execution_result_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    decided_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sync_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    website_count INTEGER NOT NULL,
    crm_account_count INTEGER NOT NULL,
    crm_contact_count INTEGER NOT NULL,
    generated_count INTEGER NOT NULL,
    skipped_decided_count INTEGER NOT NULL,
    new_pending_count INTEGER NOT NULL
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StateStore:
    """Stores proposal decisions so daily syncs never re-propose resolved items."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    def upsert_proposals(
        self,
        proposals: list[Proposal],
        website_count: int,
        crm_account_count: int,
        crm_contact_count: int,
    ) -> dict[str, int]:
        """Insert or refresh pending proposals; preserve previously decided rows unchanged."""
        now = utc_now_iso()
        skipped_decided = 0
        new_pending = 0
        active_fingerprints = {p.fingerprint for p in proposals}

        with self._connect() as conn:
            # Remove stale pending proposals that resolved organically in CRM, keeping audit history of decided ones.
            existing_pending = conn.execute(
                "SELECT fingerprint FROM proposals WHERE decision_status = 'pending'"
            ).fetchall()
            for row in existing_pending:
                if row["fingerprint"] not in active_fingerprints:
                    conn.execute("DELETE FROM proposals WHERE fingerprint = ?", (row["fingerprint"],))

            for prop in proposals:
                existing = conn.execute(
                    "SELECT decision_status FROM proposals WHERE fingerprint = ?",
                    (prop.fingerprint,),
                ).fetchone()
                if existing and existing["decision_status"] in ("approved", "rejected"):
                    skipped_decided += 1
                    conn.execute(
                        "UPDATE proposals SET last_seen_at = ? WHERE fingerprint = ?",
                        (now, prop.fingerprint),
                    )
                    continue

                if existing is None:
                    new_pending += 1
                    conn.execute(
                        """
                        INSERT INTO proposals (
                            fingerprint, category, title, facility_name, target_account_id,
                            recommended_decision, confidence, summary, evidence_json,
                            operations_json, decision_status, first_seen_at, last_seen_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                        """,
                        (
                            prop.fingerprint,
                            prop.category,
                            prop.title,
                            prop.facility_name,
                            prop.target_account_id,
                            prop.recommended_decision,
                            prop.confidence,
                            prop.summary,
                            json.dumps(prop.evidence),
                            json.dumps(prop.operations),
                            now,
                            now,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE proposals
                        SET category = ?, title = ?, facility_name = ?, target_account_id = ?,
                            recommended_decision = ?, confidence = ?, summary = ?,
                            evidence_json = ?, operations_json = ?, last_seen_at = ?
                        WHERE fingerprint = ? AND decision_status = 'pending'
                        """,
                        (
                            prop.category,
                            prop.title,
                            prop.facility_name,
                            prop.target_account_id,
                            prop.recommended_decision,
                            prop.confidence,
                            prop.summary,
                            json.dumps(prop.evidence),
                            json.dumps(prop.operations),
                            now,
                            prop.fingerprint,
                        ),
                    )

            conn.execute(
                """
                INSERT INTO sync_runs (
                    started_at, website_count, crm_account_count, crm_contact_count,
                    generated_count, skipped_decided_count, new_pending_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    website_count,
                    crm_account_count,
                    crm_contact_count,
                    len(proposals),
                    skipped_decided,
                    new_pending,
                ),
            )

        return {
            "generated": len(proposals),
            "skipped_decided": skipped_decided,
            "new_pending": new_pending,
        }

    def list_proposals(self, status: str | None = None) -> list[Proposal]:
        query = "SELECT * FROM proposals"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE decision_status = ?"
            params = (status,)
        query += " ORDER BY CASE decision_status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, category, facility_name"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_proposal(r) for r in rows]

    def get_proposal(self, fingerprint: str) -> Proposal | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM proposals WHERE fingerprint = ?", (fingerprint,)).fetchone()
            if row is not None:
                return self._row_to_proposal(row)
            rows = conn.execute(
                "SELECT * FROM proposals WHERE fingerprint LIKE ?",
                (f"{fingerprint}%",),
            ).fetchall()
            if len(rows) == 1:
                return self._row_to_proposal(rows[0])
            return None

    def mark_approved(self, fingerprint: str, execution_result: dict[str, Any], note: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE proposals
                SET decision_status = 'approved', decision_note = ?,
                    execution_result_json = ?, decided_at = ?
                WHERE fingerprint = ?
                """,
                (note, json.dumps(execution_result), utc_now_iso(), fingerprint),
            )

    def mark_rejected(self, fingerprint: str, note: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE proposals
                SET decision_status = 'rejected', decision_note = ?, decided_at = ?
                WHERE fingerprint = ?
                """,
                (note or "Rejected by reviewer", utc_now_iso(), fingerprint),
            )

    def latest_sync_run(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sync_runs ORDER BY run_id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT decision_status, COUNT(*) AS cnt FROM proposals GROUP BY decision_status"
            ).fetchall()
        out = {"pending": 0, "approved": 0, "rejected": 0}
        for r in rows:
            out[r["decision_status"]] = int(r["cnt"])
        return out

    @staticmethod
    def _row_to_proposal(row: sqlite3.Row) -> Proposal:
        return Proposal(
            fingerprint=row["fingerprint"],
            category=row["category"],
            title=row["title"],
            facility_name=row["facility_name"],
            target_account_id=row["target_account_id"],
            recommended_decision=row["recommended_decision"],
            confidence=row["confidence"],
            summary=row["summary"],
            evidence=json.loads(row["evidence_json"]),
            operations=json.loads(row["operations_json"]),
            decision_status=row["decision_status"],
            decision_note=row["decision_note"],
            execution_result=json.loads(row["execution_result_json"] or "{}"),
        )
