"""Alembic environment.

Offline mode is deliberately unsupported: Meridian's migrations include CHECK
constraints that carry product meaning, and generating SQL to be applied by hand
somewhere else is exactly how those stop being applied.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

DEFAULT_URL = "postgresql://meridian:meridian@localhost:5433/meridian"


def database_url() -> str:
    """The connection string, pointed at psycopg 3.

    SQLAlchemy still resolves a bare `postgresql://` to psycopg2, which this
    project does not install. Normalizing here means the same URL works for the
    repository layer and for migrations, rather than the two disagreeing about
    what "postgres" means.
    """
    url = os.environ.get("MERIDIAN_DATABASE_URL", DEFAULT_URL)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():  # pragma: no cover
    raise RuntimeError(
        "offline migrations are not supported: the schema carries CHECK constraints "
        "with product meaning, and hand-applied SQL is how those stop being applied"
    )

run_migrations_online()
