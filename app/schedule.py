"""Render launchd/cron schedules only; loading a LaunchAgent belongs to setup.sh."""

import argparse
import plistlib
import shlex
from pathlib import Path

from app.settings import ROOT

LABEL = "ru.bio-job-agent.watch"
TEMPLATE = ROOT / "launchd" / f"{LABEL}.plist"


def valid_hours(hours):
    if (
        len(hours) not in (1, 2)
        or len(set(hours)) != len(hours)
        or any(not 0 <= h <= 23 for h in hours)
    ):
        raise ValueError("Choose one or two distinct hours from 0 to 23")
    return sorted(hours)


def render_plist(project, uv, home, hours):
    hours = valid_hours(hours)
    template = plistlib.loads(TEMPLATE.read_bytes())
    replacements = {"__DIR__": str(project), "__UV__": str(uv), "__HOME__": str(home)}

    def replace(value):
        if isinstance(value, str):
            for marker, replacement in replacements.items():
                value = value.replace(marker, replacement)
            return value
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    result = replace(template)
    result["StartCalendarInterval"] = [{"Hour": hour, "Minute": 0} for hour in hours]
    return plistlib.dumps(result, sort_keys=False)


def cron_line(project, uv, home, hours):
    hours = valid_hours(hours)
    # WorkingDirectory is important: native CLI backends may resolve local relative paths.
    command = (
        f"(cd {shlex.quote(str(project))} && "
        + shlex.join(
            [str(uv), "run", "--project", str(project), "bio-jobs", "watch", "--scheduled"]
        )
        + f") >> {shlex.quote(str(project / 'logs/watch.log'))} 2>&1"
    )
    command = command.replace("%", r"\%")
    path = f"{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    return f"PATH={shlex.quote(path)}\n0 {','.join(map(str, hours))} * * * {command}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--hours", type=int, nargs="+", default=[10])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output:
            rendered = render_plist(args.project, args.uv, args.home, args.hours)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(rendered)
        else:
            print(cron_line(args.project, args.uv, args.home, args.hours))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
