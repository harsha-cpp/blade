from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260904_0009"
down_revision = "20260904_0008"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_BACKFILL_SEQ = """
    WITH ordered AS (
        SELECT
            id,
            row_number() OVER (PARTITION BY org_id ORDER BY created_at, id) AS position
        FROM audit_events
    )
    UPDATE audit_events
    SET seq = ordered.position
    FROM ordered
    WHERE audit_events.id = ordered.id
"""

_NO_UPDATE_TRIGGER = """
    CREATE TRIGGER audit_events_no_update
    BEFORE UPDATE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION block_audit_event_mutation()
"""

_NO_TRUNCATE_TRIGGER = """
    CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION block_audit_event_mutation()
"""


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("seq", sa.BigInteger(), nullable=True))
    op.add_column("audit_events", sa.Column("row_hmac", sa.Text(), nullable=True))

    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events")
    op.execute(_BACKFILL_SEQ)
    op.execute(_NO_UPDATE_TRIGGER)

    op.alter_column("audit_events", "seq", nullable=False)
    op.create_unique_constraint("uq_audit_events_org_seq", "audit_events", ["org_id", "seq"])
    op.execute(_NO_TRUNCATE_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.drop_constraint("uq_audit_events_org_seq", "audit_events", type_="unique")
    op.drop_column("audit_events", "row_hmac")
    op.drop_column("audit_events", "seq")
