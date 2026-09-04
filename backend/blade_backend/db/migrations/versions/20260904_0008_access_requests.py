from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260904_0008"
down_revision = "20260904_0007"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "access_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("organization", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("size", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("client_ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'new'")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handled_by", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('new','contacted','approved','declined')",
            name="ck_access_requests_status",
        ),
        sa.CheckConstraint(
            "size IS NULL OR size IN ('under-100','100-500','500-1000','over-1000')",
            name="ck_access_requests_size",
        ),
    )
    op.create_index("ix_access_requests_created", "access_requests", ["created_at"])
    op.create_index("ix_access_requests_email_created", "access_requests", ["email", "created_at"])
    op.create_index(
        "ix_access_requests_status_created", "access_requests", ["status", "created_at"]
    )
    op.create_index("ix_org_members_email", "org_members", ["email"])


def downgrade() -> None:
    op.drop_index("ix_org_members_email", table_name="org_members")
    op.drop_index("ix_access_requests_status_created", table_name="access_requests")
    op.drop_index("ix_access_requests_email_created", table_name="access_requests")
    op.drop_index("ix_access_requests_created", table_name="access_requests")
    op.drop_table("access_requests")
