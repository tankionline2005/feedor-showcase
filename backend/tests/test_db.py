from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import _add_missing_columns


async def test_missing_columns_are_added_to_an_old_table(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'old.db').as_posix()}")
    async with engine.begin() as conn:
        # an accounts table as deployed before proxies existed
        await conn.execute(text("CREATE TABLE accounts (id INTEGER PRIMARY KEY, session TEXT NOT NULL)"))
        await conn.execute(text("INSERT INTO accounts (id, session) VALUES (1, 's')"))
        await conn.run_sync(_add_missing_columns)
        columns = await conn.run_sync(lambda c: {col["name"] for col in inspect(c).get_columns("accounts")})
        row = (await conn.execute(text("SELECT proxy_id, enabled, max_channels FROM accounts"))).one()
    await engine.dispose()
    assert {"proxy_id", "enabled", "status", "max_channels"} <= columns
    assert row == (None, 1, 300)  # defaults filled in for existing rows
