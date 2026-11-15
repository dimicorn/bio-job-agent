import sqlite3

import pytest

from app import db


def test_failed_migration_rolls_back_schema(conn, tmp_path, monkeypatch):
    folder = tmp_path / "bad_migrations"
    folder.mkdir()
    (folder / "999_broken.sql").write_text(
        "CREATE TABLE should_rollback(id INTEGER);\nINVALID SQL;\n"
    )
    monkeypatch.setattr(db, "MIGRATIONS", folder)
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(conn)
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='should_rollback'").fetchone()
    assert not conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version='999_broken.sql'"
    ).fetchone()
