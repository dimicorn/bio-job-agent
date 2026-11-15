import sqlite3
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@contextmanager
def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)")
    conn.commit()
    for path in sorted(MIGRATIONS.glob("[0-9]*.sql")):
        # Check under the lock so simultaneous CLI processes migrate once.
        conn.execute("BEGIN IMMEDIATE")
        try:
            if not conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?", (path.name,)
            ).fetchone():
                statement = ""
                for line in path.read_text().splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        conn.execute(statement)
                        statement = ""
                if statement.strip():
                    raise ValueError(f"Incomplete SQL migration: {path.name}")
                conn.execute("INSERT INTO schema_migrations VALUES (?)", (path.name,))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
