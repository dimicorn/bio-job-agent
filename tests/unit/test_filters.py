import pytest
from pydantic import ValidationError

from app.models.posting import Posting, SourceConfig
from app.pipeline.filter import exclusion, geography
from app.pipeline.normalize import canonical_url, location_key, text


def posting(**kwargs):
    return Posting(
        external_id="1",
        title="Principal Scientist",
        employer="Industry Bio",
        source_url="https://example.com/jobs/1",
        apply_url="https://example.com/jobs/1",
        **kwargs,
    )


@pytest.mark.parametrize(
    "location,expected",
    [
        ("London, England", "london"),
        ("Greater London", "london"),
        ("Hammersmith", "london"),
        ("Croydon, UK", "london"),
        ("Cambridge, UK", "outside"),
        ("Slough", "outside"),
        ("Stevenage", "outside"),
        ("Uxbridge", "outside"),
        ("Oxford", "outside"),
        ("Londonderry", "outside"),
        ("London, Ontario", "outside"),
        ("New London, Connecticut", "outside"),
        ("London, United States", "outside"),
        ("London, ON", "outside"),
        ("Uxbridge, London", "outside"),
        (None, "unknown"),
        ("Remote", "unknown"),
        ("Remote - United Kingdom", "unknown"),
        ("United Kingdom", "unknown"),
    ],
)
def test_geography(profile, location, expected):
    assert geography(posting(location=location), profile) == expected


@pytest.mark.parametrize("region", ["slough", "stevenage", "uxbridge", "cambridge", "oxford"])
def test_independent_regions(profile, region):
    profile.regions.london = False
    setattr(profile.regions, region, True)
    assert geography(posting(location=region.title()), profile) == region
    assert geography(posting(location="London"), profile) == "outside"
    for other in {"slough", "stevenage", "uxbridge", "cambridge", "oxford"} - {region}:
        assert geography(posting(location=other), profile) == "outside"


def test_foreign_country(profile):
    assert geography(posting(location="London", country="CA"), profile) == "outside"


@pytest.mark.parametrize(
    "employer,title",
    [
        ("University of London", "Principal Scientist"),
        ("Imperial College London", "Principal Scientist"),
        ("Industry Bio", "Principal Scientist / Postdoctoral Fellow"),
        ("Industry Bio", "Principal Scientist PhD Studentship"),
        ("Industry Bio", "Principal Scientist Sales"),
        ("Industry Bio", "Professor of Biology"),
    ],
)
def test_academic_and_sales_excluded(profile, sources, employer, title):
    job = posting(location="London").model_copy(update={"employer": employer, "title": title})
    assert exclusion(job, sources[0], profile)


def test_phd_requirement_not_excluded(profile, sources):
    job = posting(location="London", description="PhD required; academic collaborations welcome.")
    assert exclusion(job, sources[0], profile) is None
    assert (
        exclusion(job, sources[0].model_copy(update={"industry_verified": False}), profile)
        == "unverified_industry"
    )


def test_unknowns_and_seniority(profile, sources):
    assert exclusion(posting(), sources[0], profile) is None
    profile.include_unknown_location = False
    assert exclusion(posting(), sources[0], profile) == "geography"
    job = posting(location="London").model_copy(update={"title": "Principal Software Engineer"})
    assert exclusion(job, sources[0], profile) == "seniority_or_role"
    job.title = "Principal PD/MSAT Scientist"
    assert exclusion(job, sources[0], profile) is None


def test_normalize_and_urls():
    assert location_key("London, England") == location_key("London, UK") == "london"
    assert (
        text("&lt;p&gt;Scientist &amp;amp; biology&lt;/p&gt;<script>bad()</script>")
        == "Scientist & biology"
    )
    assert (
        canonical_url("https://EXAMPLE.com/job/12/?gh_src=x&utm_medium=y#top")
        == "https://example.com/job/12"
    )
    assert canonical_url("https://example.com/job?id=12") != canonical_url(
        "https://example.com/job?id=13"
    )


def test_no_linkedin_or_unsafe_apply_links():
    for url in [
        "javascript:alert(1)",
        "http://example.com",
        "https://www.linkedin.com/jobs/1",
        "https://user:secret@example.com/jobs",
    ]:
        with pytest.raises(ValidationError):
            Posting(**{**posting().model_dump(), "apply_url": url})


def test_sources_are_industry_allowlist(sources):
    for update in [{"industry": "academic"}, {"board": "../../etc"}, {"min_interval_seconds": 0}]:
        with pytest.raises(ValidationError):
            SourceConfig.model_validate({**sources[0].model_dump(), **update})
