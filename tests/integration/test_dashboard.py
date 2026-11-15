from streamlit.testing.v1 import AppTest

from app.settings import ROOT


def test_dashboard_native_render(tmp_path, monkeypatch):
    monkeypatch.setenv("BIO_DATABASE_PATH", str(tmp_path / "ui.sqlite3"))
    app = AppTest.from_file(str(ROOT / "app/dashboard/main.py")).run(timeout=20)
    assert not app.exception
    assert app.title[0].value == "London biotech jobs"
    assert any("No matching roles" in x.value for x in app.info)


def test_dashboard_populated_feed(conn, tmp_path, profile, sources, transport, monkeypatch):
    from app.pipeline.discover import discover

    discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    monkeypatch.setenv("BIO_DATABASE_PATH", str(tmp_path / "ui.sqlite3"))
    # Use the already migrated fixture database, without another network fetch.
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    monkeypatch.setenv("BIO_DATABASE_PATH", path)
    app = AppTest.from_file(str(ROOT / "app/dashboard/main.py")).run(timeout=20)
    assert not app.exception
    assert any("Principal Scientist" in x.value for x in app.subheader)
    assert len(app.get("link_button")) == 4  # Two apply links and two provenance links.
    assert any("2 matching roles" in x.value for x in app.markdown)
    app.sidebar.text_input[0].set_value("no such role").run()
    assert not app.exception
    assert not app.subheader
