from __future__ import annotations

from alembic import op

revision = "20260904_0007"
down_revision = "20260829_0006"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_attachment_scans_status", "attachment_scans", type_="check")
    op.create_check_constraint(
        "ck_attachment_scans_status",
        "attachment_scans",
        "status IN ('scanned','partial','queued','failed')",
    )


def downgrade() -> None:
    op.execute("UPDATE attachment_scans SET status = 'failed' WHERE status = 'partial'")
    op.drop_constraint("ck_attachment_scans_status", "attachment_scans", type_="check")
    op.create_check_constraint(
        "ck_attachment_scans_status",
        "attachment_scans",
        "status IN ('scanned','queued','failed')",
    )
