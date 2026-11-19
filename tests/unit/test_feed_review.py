from types import SimpleNamespace

import pytest

from app.sources.feed_review import review_feed


def item(
    name="Novel Bio",
    title="Scientist",
    locality="London",
    url="https://biotechnologyjobs.co.uk/jobs/one",
):
    return {
        "url": url,
        "_jobposting": {
            "title": title,
            "hiringOrganization": {"name": name},
            "jobLocation": {"address": {"addressCountry": "GB", "addressLocality": locality}},
        },
    }


def test_leads_remain_unreviewed_and_known_names_are_marked():
    novel = item()
    novel["external_url"] = "https://jobs.lever.co/novel/123"
    known = item(name="Known Bio", url="https://biotechnologyjobs.co.uk/jobs/two")
    leads = review_feed({"items": [novel, novel, known]}, [SimpleNamespace(name="Known Bio")])
    assert len(leads) == 2
    assert leads[0]["employer_review"] == "unreviewed"
    assert leads[0]["original_ats_url"] == "https://jobs.lever.co/novel/123"
    assert leads[1]["employer_review"] == "reviewed"


def test_noise_geography_and_untrusted_links_are_excluded():
    bad_link = item()
    bad_link["external_url"] = "https://jobs.lever.co.evil.example/novel/123"
    leads = review_feed(
        {
            "items": [
                item(name="University of London"),
                item(name="Recruitment Ltd"),
                item(locality="Cambridge"),
                item(title="Sales Director"),
                bad_link,
            ]
        },
        [],
    )
    assert len(leads) == 1
    assert leads[0]["original_ats_url"] is None


def test_malformed_feed_is_rejected():
    with pytest.raises(TypeError):
        review_feed({}, [])
