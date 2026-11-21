# Development and tests

CLI export uses current YAML preferences and includes source provenance:

```sh
uv run bio-jobs export jobs.csv
uv run ruff check app tests
uv run ruff format --check app tests
uv run pytest
```

Default tests use recorded/sanitized fixtures and block network sockets. They cover real API shapes, pagination, deduplication, industry/geography exclusions, partial/malformed feeds, failed sources, robots restrictions, cooldown/429 handling, transactional migrations, profile versions, CSV formula safety, terminal list/review/export/setup/doctor flows and all seven LLM backends with mocked HTTP/subprocesses.

Live checks are separate and explicit; they use a temporary database and never submit anything:

```sh
uv run pytest --live -m live -s
# One synthetic scoring request only (Codex by default; no personal resume):
BIO_LIVE_SCORING=1 uv run pytest tests/integration/test_matching.py --live -m live -s
```

The GitHub Actions [tests workflow](../.github/workflows/tests.yml) runs ruff and the offline test suite (with Chromium installed) on every push and pull request.

Commit conventions, the protected `main` branch and the release steps (CHANGELOG via git-cliff, versions from git tags) are in [CONTRIBUTING.md](../CONTRIBUTING.md); release history is in [CHANGELOG.md](../CHANGELOG.md).
