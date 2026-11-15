import sys
from pathlib import Path

# Support both `streamlit run app/dashboard/main.py` and the installed package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

from app.candidate.profile import import_profile, load_profile
from app.db import connect, migrate
from app.pipeline.discover import discover
from app.settings import Settings
from app.sources.config import load_sources
from app.store import export_csv, list_jobs


def main():
    st.set_page_config(page_title="London biotech jobs", layout="wide")
    st.title("London biotech jobs")
    st.caption(
        "Industry scientist discovery • Local storage • Open employer links to apply manually"
    )
    settings = Settings()
    try:
        profile = load_profile(settings.resolved(settings.candidate_path))
        sources = load_sources(settings.resolved(settings.sources_path))
    except (OSError, ValueError) as exc:
        st.error(f"Check your profile and source configuration: {exc}")
        return
    with connect(settings.resolved(settings.database_path)) as conn:
        migrate(conn)
        version = import_profile(conn, profile)
        conn.commit()
        if not profile.reviewed:
            st.info(
                "Using unreviewed example preferences. Edit your candidate YAML and mark reviewed after checking it."
            )
        with st.sidebar:
            st.caption(f"Profile version {version}")
            if st.button("Refresh public sources", type="primary"):
                with st.spinner("Checking public employer boards…"):
                    results = discover(
                        conn,
                        profile,
                        sources,
                        timeout=settings.http_timeout,
                        user_agent=settings.user_agent,
                    )
                for result in results:
                    if result["status"] in {"error", "partial"}:
                        st.warning(
                            f"{result['source']}: {result['status']}. See source health below."
                        )
                    else:
                        st.caption(f"{result['source']}: {result['status']}")
            search = st.text_input("Search title or description")
        jobs = list_jobs(conn, profile, sources)
        employers = st.sidebar.multiselect("Employers", sorted({j["employer"] for j in jobs}))
        unknown = st.sidebar.checkbox("Include unknown location (needs review)", value=True)
        jobs = [
            j
            for j in jobs
            if (not employers or j["employer"] in employers)
            and (unknown or j["geography"] != "unknown")
            and (
                not search or search.casefold() in (j["title"] + " " + j["description"]).casefold()
            )
        ]
        st.download_button(
            "Download filtered CSV",
            export_csv(jobs).encode("utf-8-sig"),
            "biotech-jobs.csv",
            "text/csv",
        )
        st.write(f"{len(jobs)} matching roles")
        if not jobs:
            st.info(
                "No matching roles stored yet. Refresh sources or adjust your YAML preferences."
            )
        for job in jobs:
            with st.container(border=True):
                st.subheader(job["title"])
                st.write(
                    f"{job['employer']} · {job['location'] or 'Unknown location — needs review'}"
                )
                st.caption(
                    f"Posted: {job['posted_at'] or 'unknown'} · Discovered: {job['discovered_at']} · Last seen: {job['last_verified_at']}"
                )
                if job["geography"] == "unknown":
                    st.caption(
                        "Location needs review: the source does not confirm London-area eligibility."
                    )
                st.link_button("Open employer application", job["apply_url"])
                with st.expander("Description and provenance"):
                    st.text(job["description"])
                    for origin in job["origins"]:
                        st.write(
                            f"{origin['source_id']} · External ID: {origin['external_id']} · Last seen: {origin['last_seen_at']}"
                        )
                        st.link_button(
                            f"Source {origin['source_id']}",
                            origin["source_url"],
                            key=f"{job['id']}-{origin['id']}",
                        )
        with st.expander("Source health"):
            health = [
                dict(x)
                for x in conn.execute(
                    "SELECT id,health,last_checked_at,last_success_at,next_allowed_at,last_error FROM sources"
                )
            ]
            st.dataframe(health, hide_index=True)
        st.caption(
            "Listings can expire between refreshes. Last seen is a discovery check, not proof an application is still open. No scoring or submission is performed."
        )


if __name__ == "__main__":
    main()
