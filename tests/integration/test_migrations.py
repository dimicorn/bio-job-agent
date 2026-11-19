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


@pytest.mark.parametrize("prior", range(1, 9))
def test_upgrade_each_prior_schema_preserves_existing_data(tmp_path, monkeypatch, prior):
    migrations = sorted(db.MIGRATIONS.glob("[0-9]*.sql"))
    assert [int(p.name[:3]) for p in migrations] == list(range(1, len(migrations) + 1))
    old = tmp_path / "old"
    old.mkdir()
    for path in migrations[:prior]:
        (old / path.name).write_text(path.read_text())
    with db.connect(tmp_path / "existing.sqlite") as conn:
        monkeypatch.setattr(db, "MIGRATIONS", old)
        db.migrate(conn)
        conn.execute(
            "INSERT INTO employers(name, industry, review_url, industry_verified) VALUES (?, ?, ?, ?)",
            ("Synthetic Labs", "biotech", "https://example.test", 1),
        )
        conn.commit()
        monkeypatch.setattr(db, "MIGRATIONS", migrations[0].parent)
        db.migrate(conn)
        db.migrate(conn)
        assert conn.execute("SELECT name FROM employers").fetchone()[0] == "Synthetic Labs"
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == len(migrations)
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='duplicate_separations'").fetchone()
