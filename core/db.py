"""PostgreSQL database connection and migration runner."""

from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        from core.config import load_config

        config = load_config()
        raw_url = config.database.url
        dsn = raw_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _run_migration(conn)


async def _run_migration(conn: asyncpg.Connection):
    """Apply the migration if tables don't exist."""
    row = await conn.fetchrow(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'memories')"
    )
    if row and row[0]:
        return

    migration = MIGRATIONS_DIR / "001_init.sql"
    sql = migration.read_text()
    await conn.execute(sql)
