"""Create iam.rc_link and iam.rc_org_map (sections 13.6, 13.7).

Revision ID: 0001_rc_link_and_org_map
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_rc_link_and_org_map"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "iam"


def upgrade() -> None:
    op.create_table(
        "rc_link",
        sa.Column("kc_user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        # NULL is allowed: users.employee_code is optional in the CRM.
        sa.Column("employee_id", sa.Text(), nullable=True),
        # NULL until the Rocket.Chat account exists (state = pending).
        sa.Column("rc_user_id", sa.Text(), nullable=True),
        sa.Column("rc_username", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("profile_hash", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'linked', 'conflict', 'deactivated', 'error')",
            name="ck_rc_link_state",
        ),
        sa.UniqueConstraint("employee_id", name="uq_rc_link_employee_id"),
        sa.UniqueConstraint("rc_user_id", name="uq_rc_link_rc_user_id"),
        schema=SCHEMA,
    )
    op.create_table(
        "rc_org_map",
        sa.Column("org_unit_code", sa.Text(), primary_key=True),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("rc_room_name", sa.Text(), nullable=True),
        sa.Column("rc_display_name", sa.Text(), nullable=True),
        sa.Column("parent_team_code", sa.Text(), nullable=True),
        sa.Column("merge_into", sa.Text(), nullable=True),
        sa.Column("rc_room_id", sa.Text(), nullable=True),
        sa.Column("rc_team_id", sa.Text(), nullable=True),
        sa.Column("managed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.CheckConstraint("level BETWEEN 1 AND 5", name="ck_rc_org_map_level"),
        sa.CheckConstraint(
            "mode IN ('team', 'team_channel', 'announce', 'none', 'merge')",
            name="ck_rc_org_map_mode",
        ),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_rc_org_map_status"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("rc_org_map", schema=SCHEMA)
    op.drop_table("rc_link", schema=SCHEMA)
