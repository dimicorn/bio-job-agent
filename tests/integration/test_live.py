import pytest

from app.pipeline.discover import discover
from app.store import list_jobs


@pytest.mark.live
def test_live_public_feeds(request, conn, profile, sources):
    if not request.config.getoption("--live"):
        pytest.skip("Use --live -m live for real network access")
    result = discover(conn, profile, sources)
    assert all(r["status"] == "ok" for r in result), result
    # Vacancy inventory changes; do not require any particular live title/count.
    assert all(r["fetched"] > 0 for r in result)
    jobs = list_jobs(conn, profile, sources)
    print(
        {
            "sources": result,
            "matching_roles": [(j["employer"], j["title"], j["location"]) for j in jobs],
        }
    )
