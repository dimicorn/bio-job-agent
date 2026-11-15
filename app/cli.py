import argparse
import json
import logging
from pathlib import Path

from app.candidate.profile import import_profile, load_profile
from app.db import connect, migrate
from app.pipeline.discover import discover
from app.settings import Settings
from app.sources.config import load_sources
from app.store import export_csv, list_jobs


def main():
    parser = argparse.ArgumentParser(description="Local London industry job discovery (M1)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Apply database migrations")
    imp = sub.add_parser(
        "import-profile", help="Version reviewed or example YAML preferences locally"
    )
    imp.add_argument("path", nargs="?", type=Path)
    sub.add_parser("sync", help="Explicit live refresh; respects robots and persistent cooldowns")
    exp = sub.add_parser("export", help="Export jobs matching current preferences")
    exp.add_argument("path", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = Settings()
    with connect(settings.resolved(settings.database_path)) as conn:
        migrate(conn)
        if args.command == "init":
            print("Database ready")
            return
        profile = load_profile(
            args.path
            if args.command == "import-profile" and args.path
            else settings.resolved(settings.candidate_path)
        )
        if args.command == "import-profile":
            print(
                json.dumps(
                    {"profile_version": import_profile(conn, profile), "reviewed": profile.reviewed}
                )
            )
            return
        sources = load_sources(settings.resolved(settings.sources_path))
        if args.command == "sync":
            results = discover(
                conn,
                profile,
                sources,
                timeout=settings.http_timeout,
                user_agent=settings.user_agent,
            )
            print(json.dumps(results, indent=2))
            if results and all(x["status"] == "error" for x in results):
                raise SystemExit(1)
        else:
            args.path.write_text(
                export_csv(list_jobs(conn, profile, sources)), encoding="utf-8-sig"
            )
            print(f"Exported to {args.path}")


if __name__ == "__main__":
    main()
