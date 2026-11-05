"""Alembic environment for the rc-sync tables in the iam schema."""

import os

from alembic import context
from sqlalchemy import create_engine, text

VERSION_TABLE = "alembic_version_rc_sync"
SCHEMA = "iam"


def run_migrations() -> None:
    url = os.environ.get("RC_SYNC_DATABASE_URL")
    if not url:
        raise RuntimeError("Set RC_SYNC_DATABASE_URL, for example postgresql+psycopg://user:pass@host/db")

    if context.is_offline_mode():
        context.configure(url=url, literal_binds=True, version_table=VERSION_TABLE, version_table_schema=SCHEMA)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(url)
    with engine.connect() as connection:
        # The version table lives in this schema, so it must exist before Alembic starts.
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        connection.commit()
        context.configure(connection=connection, version_table=VERSION_TABLE, version_table_schema=SCHEMA)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
