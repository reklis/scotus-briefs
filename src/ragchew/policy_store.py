"""PostgreSQL audit persistence for policy decisions."""

from __future__ import annotations

import json
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ragchew.policy import PolicyDecision


class PostgresPolicyStore:
    def __init__(
        self,
        dsn: str,
        pool: ConnectionPool[Connection[dict[str, Any]]] | None = None,
    ) -> None:
        self.pool = pool or ConnectionPool(
            dsn,
            kwargs={"row_factory": dict_row},
            min_size=1,
            max_size=5,
            open=True,
        )

    def save(self, decision: PolicyDecision) -> PolicyDecision:
        with self.pool.connection() as connection, connection.transaction():
            connection.execute(
                """INSERT INTO policy_decisions
                   (decision_id,incident_id,eligible,policy_version,reasons,
                    approved_claims,created_at)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                   ON CONFLICT(decision_id) DO NOTHING""",
                (
                    decision.decision_id,
                    decision.incident_id,
                    decision.eligible,
                    decision.policy_version,
                    json.dumps(list(decision.reasons)),
                    json.dumps(
                        [claim.model_dump(mode="json") for claim in decision.approved_claims],
                        default=str,
                    ),
                    decision.created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM policy_decisions WHERE decision_id=%s",
                (decision.decision_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("policy decision disappeared")
        claims = row["approved_claims"]
        reasons = row["reasons"]
        if isinstance(claims, str):
            claims = json.loads(claims)
        if isinstance(reasons, str):
            reasons = json.loads(reasons)
        return PolicyDecision.model_validate(
            {
                "decision_id": row["decision_id"],
                "incident_id": row["incident_id"],
                "eligible": row["eligible"],
                "policy_version": row["policy_version"],
                "reasons": reasons,
                "approved_claims": claims,
                "created_at": row["created_at"],
            }
        )
