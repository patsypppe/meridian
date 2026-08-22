"""The initial schema.

This first migration applies `store/schema.sql` verbatim, which is deliberate:
the canonical file *is* the baseline, so there is exactly one place to read the
starting shape of the database and no chance of the two disagreeing on day one.

Every migration after this one is written by hand, and from that point the drift
test earns its keep — it applies the migration chain to one empty database and
`schema.sql` to another, and diffs the results. The moment someone alters a table
and forgets the readable copy, that test fails.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schema.sql"

TABLES = (
    "score",
    "trial",
    "run",
    "task_provenance",
    "task",
    "suite_version",
    "tenant",
)


def upgrade() -> None:
    op.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
