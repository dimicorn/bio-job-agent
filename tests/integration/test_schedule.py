import json
import os
import plistlib
import pty
import shlex
import subprocess
import sys

import pytest

from app import schedule
from app.settings import ROOT


@pytest.mark.parametrize("hours", [[9], [9, 18]])
def test_plist_render_to_temp_path_only(tmp_path, hours):
    project = tmp_path / "Research & jobs"
    home = tmp_path / "Home & user"
    output = tmp_path / "watch.plist"
    schedule.main(
        [
            "--project",
            str(project),
            "--uv",
            "/opt/uv & tools/uv",
            "--home",
            str(home),
            "--hours",
            *map(str, hours),
            "--output",
            str(output),
        ]
    )
    data = plistlib.loads(output.read_bytes())
    assert data["Label"] == "ru.bio-job-agent.watch"
    assert data["RunAtLoad"] is False
    assert data["StartCalendarInterval"] == [{"Hour": hour, "Minute": 0} for hour in hours]
    assert data["ProgramArguments"] == [
        "/opt/uv & tools/uv",
        "run",
        "--project",
        str(project),
        "bio-jobs",
        "watch",
        "--scheduled",
    ]
    assert data["WorkingDirectory"] == str(project)
    assert data["StandardOutPath"] == data["StandardErrorPath"] == str(project / "logs/watch.log")
    assert str(home / ".local/bin") in data["EnvironmentVariables"]["PATH"]
    assert "/opt/homebrew/bin" in data["EnvironmentVariables"]["PATH"]


@pytest.mark.parametrize("hours", [[], [24], [-1], [9, 9], [1, 2, 3]])
def test_invalid_schedule_hours_rejected(tmp_path, hours):
    with pytest.raises(ValueError):
        schedule.render_plist(tmp_path, tmp_path / "uv", tmp_path, hours)


def test_cron_line_contains_safe_paths_and_scheduled_flag(tmp_path):
    project = tmp_path / "job's folder"
    line = schedule.cron_line(project, tmp_path / "uv", tmp_path, [18, 9])
    assert "0 9,18 * * * " in line
    assert "watch --scheduled" in line
    assert shlex.quote(str(project)) in line
    assert "logs/watch.log" in line
    assert "/.local/bin:/opt/homebrew/bin" in line
    percent = schedule.cron_line(tmp_path / "100% jobs", tmp_path / "uv", tmp_path, [10])
    assert r"100\% jobs" in percent


def test_setup_schedule_opt_in_rerun_and_default_no(tmp_path):
    # A real terminal drives only fake uv/uname/launchctl executables. No host LaunchAgent loaded.
    binary_dir = tmp_path / ".local/bin"
    binary_dir.mkdir(parents=True)
    record = tmp_path / "launch-calls.jsonl"
    uv = binary_dir / "uv"
    uv.write_text(f"""#!{sys.executable}
import subprocess, sys
if 'app.schedule' in sys.argv:
    index = sys.argv.index('app.schedule')
    raise SystemExit(subprocess.call([sys.executable, '-m', 'app.schedule', *sys.argv[index+1:]], cwd={str(ROOT)!r}))
""")
    uname = binary_dir / "uname"
    uname.write_text("#!/bin/sh\necho Darwin\n")
    launchctl = binary_dir / "launchctl"
    launchctl.write_text(f"""#!{sys.executable}
import json, sys
with open({str(record)!r}, 'a') as handle:
    handle.write(json.dumps(sys.argv[1:]) + '\\n')
""")
    for binary in (uv, uname, launchctl):
        binary.chmod(0o755)
    env = {**os.environ, "HOME": str(tmp_path), "PATH": f"{binary_dir}:{os.environ['PATH']}"}
    # Preserve the instruction to keep generated logs outside the worktree even in the installer test.
    script = tmp_path / "setup.sh"
    script.write_text((ROOT / "setup.sh").read_text())
    for responses, expected_hours in [
        (b"y\n09\nn\n", [9]),
        (b"y\n08\ny\n18\n", [8, 18]),
        (b"n\n", [8, 18]),
    ]:
        master, slave = pty.openpty()
        try:
            child = subprocess.Popen(
                ["bash", str(script)],
                stdin=slave,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=tmp_path,
            )
            os.write(master, responses)
            stdout, stderr = child.communicate(timeout=15)
            assert child.returncode == 0, (stdout, stderr)
        finally:
            os.close(master)
            os.close(slave)
        agent = tmp_path / "Library/LaunchAgents/ru.bio-job-agent.watch.plist"
        data = plistlib.loads(agent.read_bytes())
        assert [x["Hour"] for x in data["StartCalendarInterval"]] == expected_hours
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    assert [row[0] for row in calls] == ["unload", "load", "unload", "load"]
    assert b"Schedule left unchanged" in stdout
