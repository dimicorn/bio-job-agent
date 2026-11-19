import json

from app.models.posting import Posting
from app.pipeline.dedupe import store_job, unmerge
from app.pipeline.discover import close_missing, register_source
from app.store import list_jobs


def seed(conn, source, job, now="2026-01-01T00:00:00+00:00"):
    employer = register_source(conn, source)
    raw = conn.execute(
        """INSERT INTO raw_postings(source_id,external_id,content_hash,payload,
        fetched_at,disposition) VALUES (?,?,?,?,?,?)""",
        (source.id, job.external_id, job.description, "{}", now, "accepted"),
    ).lastrowid
    return store_job(conn, job, source.id, employer, raw, now)


def posting(source, **changes):
    fields = {
        "external_id": "42",
        "title": "Principal Scientist",
        "employer": source.employer,
        "location": "London, UK",
        "description": "Study molecular biology and develop new therapies. " * 6,
        "source_url": "https://example.org/a",
        "apply_url": "https://example.org/a",
    }
    fields.update(changes)
    return Posting(**fields)


def test_scoped_owner_collision_and_expiry_handover(conn, sources):
    a = sources[0]
    b = a.model_copy(update={"id": "other"})
    first = posting(a)
    second = posting(b, source_url="https://example.org/b", apply_url="https://example.org/b")
    job_id = seed(conn, a, first)
    assert seed(conn, b, second) == job_id
    updated = second.model_copy(update={"description": "Updated from B"})
    assert seed(conn, b, updated) == job_id
    assert (
        json.loads(conn.execute("SELECT payload FROM jobs").fetchone()[0])["description"]
        == first.description
    )
    close_missing(conn, a.id, set())
    close_missing(conn, a.id, set())
    assert (
        json.loads(conn.execute("SELECT payload FROM jobs").fetchone()[0])["description"]
        == "Updated from B"
    )
    refreshed = first.model_copy(update={"description": "Returned from A"})
    seed(conn, a, refreshed)
    assert (
        json.loads(conn.execute("SELECT payload FROM jobs").fetchone()[0])["description"]
        == "Updated from B"
    )


def test_probable_group_unmerge_and_distinct_requisitions(conn, profile, sources):
    a = sources[0]
    b = a.model_copy(update={"id": "other"})
    first = posting(
        a, description="Req: ABC123. " + "Study molecular biology and develop new therapies. " * 6
    )
    second = posting(
        b,
        title="Principal Scientist, the",
        location="London / Cambridge",
        source_url="https://example.org/b",
        apply_url="https://example.org/b",
        description=first.description + " Additional details.",
    )
    job_id = seed(conn, a, first)
    assert seed(conn, b, second) == job_id
    rows = list_jobs(conn, profile, [a, b])
    assert set(rows[0]["locations"]) == {"London, UK", "London / Cambridge"}
    origin = conn.execute("SELECT id FROM job_sources WHERE source_id=?", (b.id,)).fetchone()[0]
    new_id = unmerge(conn, job_id, origin)
    seed(conn, b, second.model_copy(update={"description": second.description + " Updated."}))
    assert (
        conn.execute("SELECT job_id FROM job_sources WHERE id=?", (origin,)).fetchone()[0] == new_id
    )
    c = a.model_copy(update={"id": "third"})
    different = posting(
        c,
        source_url="https://example.org/c",
        apply_url="https://example.org/c",
        description=first.description.replace("ABC123", "DEF456"),
    )
    assert seed(conn, c, different) not in (job_id, new_id)


def test_application_job_is_not_merged_into(conn, sources):
    a = sources[0]
    b = a.model_copy(update={"id": "other"})
    first = posting(a)
    job_id = seed(conn, a, first)
    conn.execute("INSERT INTO applications(job_id,state) VALUES (?, 'saved')", (job_id,))
    assert seed(conn, b, first.model_copy(update={"source_url": "https://example.org/b"})) != job_id


def test_explicit_teams_are_distinct(conn, sources):
    a = sources[0]
    b = a.model_copy(update={"id": "other"})
    description = "Study molecular biology and develop new therapies. " * 6
    first = posting(a, description="Team: Oncology\n" + description)
    second = posting(
        b,
        description="Team: Immunology\n" + description,
        source_url="https://example.org/b",
        apply_url="https://example.org/b",
    )
    assert seed(conn, a, first) != seed(conn, b, second)


def test_existing_jobs_group_without_moving_history_and_unmerge(conn, profile, sources):
    from app.candidate.profile import import_profile

    a = sources[0]
    b = a.model_copy(update={"id": "old-other"})
    first = posting(a)
    job_a = seed(conn, a, first)
    conn.execute("INSERT INTO applications(job_id,state) VALUES (?, 'saved')", (job_a,))
    second = posting(
        b,
        location="London / Cambridge",
        source_url="https://example.org/b",
        apply_url="https://example.org/b",
        description=first.description + " Details.",
    )
    job_b = seed(conn, b, second)
    assert job_b != job_a
    conn.execute("DELETE FROM applications WHERE job_id=?", (job_a,))
    conn.execute("INSERT INTO job_reviews(job_id,decision) VALUES (?, 'later')", (job_b,))
    profile_id = import_profile(conn, profile)
    conn.execute(
        """INSERT INTO evaluations(job_id,content_hash,scoring_version,profile_version,
        payload,weights) VALUES (?, 'hash', 'v1', ?, '{}', '{}')""",
        (job_b, profile_id),
    )
    conn.execute(
        """INSERT INTO material_versions(job_id,profile_version,kind,content,path,warnings,
        provenance,job_hash) VALUES (?, ?, 'letter', 'draft', 'draft.md', '[]', '{}', 'hash')""",
        (job_b, profile_id),
    )
    for _ in range(2):
        grouped = list_jobs(conn, profile, [a, b])
        assert len(grouped) == 1
        assert grouped[0]["grouped_job_ids"] == [job_a, job_b]
        assert set(grouped[0]["locations"]) == {"London, UK", "London / Cambridge"}
        assert len(grouped[0]["origins"]) == 2
    assert conn.execute("SELECT job_id FROM job_reviews").fetchone()[0] == job_b
    assert conn.execute("SELECT job_id FROM evaluations").fetchone()[0] == job_b
    assert conn.execute("SELECT job_id FROM material_versions").fetchone()[0] == job_b
    origin_b = conn.execute("SELECT id FROM job_sources WHERE job_id=?", (job_b,)).fetchone()[0]
    assert unmerge(conn, job_a, origin_b) == job_b
    assert len(list_jobs(conn, profile, [a, b])) == 2
    assert len(list_jobs(conn, profile, [a, b])) == 2


def test_existing_application_jobs_remain_visible(conn, profile, sources):
    a = sources[0]
    b = a.model_copy(update={"id": "other"})
    first = posting(a)
    job_a = seed(conn, a, first)
    conn.execute("INSERT INTO applications(job_id,state) VALUES (?, 'submitted')", (job_a,))
    job_b = seed(conn, b, first.model_copy(update={"source_url": "https://example.org/b"}))
    rows = list_jobs(conn, profile, [a, b])
    assert len(rows) == 1 and rows[0]["id"] == job_a
    conn.execute("INSERT INTO applications(job_id,state) VALUES (?, 'preparing')", (job_b,))
    assert {row["id"] for row in list_jobs(conn, profile, [a, b])} == {job_a, job_b}
