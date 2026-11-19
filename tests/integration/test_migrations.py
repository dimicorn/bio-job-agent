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
        conn.execute(
            "INSERT INTO sources(id,employer_id,adapter,board,config) VALUES ('s',1,'test','test','{}')"
        )
        conn.execute(
            """INSERT INTO raw_postings(source_id,external_id,content_hash,payload,fetched_at,disposition)
            VALUES ('s','raw','hash','{}','now','accepted')"""
        )
        for job_id, payload in [(1, '{"source_url":"match"}'), (2, "{}"), (3, "{}")]:
            conn.execute(
                """INSERT INTO jobs(id,employer_id,title_key,location_key,canonical_url,payload,
                description,discovered_at,last_verified_at) VALUES (?,1,'title','location','url',?,'d','now','now')""",
                (job_id, payload),
            )
        for origin_id, job_id, url in [
            (1, 1, "other"),
            (2, 1, "match"),
            (3, 1, "match"),
            (4, 2, "other"),
            (5, 2, "another"),
        ]:
            conn.execute(
                """INSERT INTO job_sources(id,job_id,source_id,external_id,source_url,apply_url,
                first_seen_at,last_seen_at,raw_posting_id) VALUES (?,?,'s',?,?,?,'now','now',1)""",
                (origin_id, job_id, str(origin_id), url, url),
            )
        conn.commit()
        monkeypatch.setattr(db, "MIGRATIONS", migrations[0].parent)
        db.migrate(conn)
        db.migrate(conn)
        assert conn.execute("SELECT name FROM employers").fetchone()[0] == "Synthetic Labs"
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == len(
            migrations
        )
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='duplicate_separations'"
        ).fetchone()
        assert [
            tuple(row) for row in conn.execute("SELECT id,owner_origin_id FROM jobs ORDER BY id")
        ] == [(1, 2), (2, 4), (3, None)]
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT id,posting_payload FROM job_sources WHERE posting_payload IS NOT NULL ORDER BY id"
            )
        ] == [(2, '{"source_url":"match"}'), (4, "{}")]
