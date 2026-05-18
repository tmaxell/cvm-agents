import ast
import asyncio
import inspect as py_inspect
import sys
import textwrap
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import db
from db import DatabaseSessionStore


async def _create_legacy_schema(engine):
    async with engine.begin() as connection:
        await connection.execute(text("""
            CREATE TABLE sessions (
                id VARCHAR(64) PRIMARY KEY,
                campaign_id INTEGER,
                title VARCHAR(255) NOT NULL,
                status VARCHAR(32) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """))
        await connection.execute(text("""
            CREATE TABLE campaign_states (
                session_id VARCHAR(64) PRIMARY KEY,
                campaign_id INTEGER,
                draft_flow_json JSON,
                draft_flow_version INTEGER,
                campaign_brief_json JSON,
                brief_completeness_json JSON,
                review_checklist_json JSON,
                review_status VARCHAR(32),
                review_checklist_acknowledged BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
            )
        """))
        await connection.execute(text("""
            INSERT INTO sessions (id, campaign_id, title, status, created_at, updated_at)
            VALUES ('legacy-session', 42, 'Legacy session', 'collect_brief', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """))
        await connection.execute(text("""
            INSERT INTO campaign_states (
                session_id,
                campaign_id,
                draft_flow_json,
                draft_flow_version,
                campaign_brief_json,
                brief_completeness_json,
                review_checklist_json,
                review_status,
                review_checklist_acknowledged,
                created_at,
                updated_at
            )
            VALUES (
                'legacy-session',
                42,
                '{}',
                1,
                '{}',
                '{}',
                '{}',
                'blocked',
                0,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
        """))


async def _column_names(engine) -> set[str]:
    def inspect_campaign_state_columns(sync_connection):
        inspector = inspect(sync_connection)
        return {column["name"] for column in inspector.get_columns("campaign_states")}

    async with engine.begin() as connection:
        return await connection.run_sync(inspect_campaign_state_columns)


async def _run_migration_smoke_test(database_url: str, monkeypatch):
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "AsyncSessionLocal", session_factory)

    try:
        await _create_legacy_schema(engine)

        assert "runtime_status" not in await _column_names(engine)

        await db.init_db()

        assert "runtime_status" in await _column_names(engine)
        async with engine.begin() as connection:
            default_value = await connection.scalar(
                text("SELECT runtime_status FROM campaign_states WHERE session_id = 'legacy-session'")
            )
        assert default_value == "editing"

        store = DatabaseSessionStore()
        sessions = await store.list_sessions()
        assert [session.id for session in sessions] == ["legacy-session"]

        await store.upsert_campaign_state(
            session_id="legacy-session",
            campaign_id=42,
            draft_flow_json={},
            runtime_status="editing",
            draft_flow_version=2,
            campaign_brief_json={},
            brief_completeness_json={},
            review_checklist_json={},
            review_status="blocked",
        )
    finally:
        await engine.dispose()


def _migration_statement_for(column_name: str) -> str:
    init_db_source = textwrap.dedent(py_inspect.getsource(db.init_db))
    init_db_tree = ast.parse(init_db_source)

    for node in ast.walk(init_db_tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "column_migrations" for target in node.targets):
            continue
        migrations = ast.literal_eval(node.value)
        return migrations[column_name]

    raise AssertionError("column_migrations assignment was not found in db.init_db()")


def test_review_checklist_acknowledged_migration_uses_postgres_boolean_default():
    statement = _migration_statement_for("review_checklist_acknowledged")

    assert "DEFAULT FALSE" in statement
    assert "DEFAULT 0" not in statement


def test_runtime_status_migration_updates_existing_sqlite_schema(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.sqlite3"

    asyncio.run(_run_migration_smoke_test(f"sqlite+aiosqlite:///{database_path}", monkeypatch))
