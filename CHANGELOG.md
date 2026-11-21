# Changelog

## [0.1.0] - 2026-11-21

### 🚀 Features

- Add prepare command; approval in review prepares missing or stale materials first
- Add review-only biotech feed pilot
- Add MIT license
- Add verified Ashby employers and handle unavailable robots
- Implement scheduled watch passes, durable job alerts and source rechecks
- Implement verified assisted and auto browser application modes
- Implement versioned application preparation and manual apply handoff
- Implement reviewed resume profiles and explainable batched job matching
- Add structured LLM backends and native terminal setup and review
- Add offline M1 acceptance tests, source fixtures and verification record
- Implement M1 local job discovery with public ATS feeds and Streamlit
- Add project plan

### 🐛 Bug Fixes

- Fix SQLite compatibility in origin migration
- Handle interruption and prevent idle sleep during long runs
- Fix candidate name extraction and letter tracing
- Handle Greenhouse widgets and visible challenges
- Handle truncated LLM answers and normalize resume evidence

### 📚 Documentation

- Add CONTRIBUTING with commit conventions and the release flow
- Restructure README, fix stale statements, move application and feed-review detail to docs

### 🎨 Styling

- Apply ruff format to app and tests (formatting only)

### 🧪 Testing

- Test upgrades from each prior schema version

### ⚙️ Miscellaneous Tasks

- Publish GitHub Releases with git-cliff notes on version tags
- Derive the package version from git tags with hatch-vcs

### 💼 Other

- Guide setup and doctor to the next step and check resume status
- Plan Lever, Workable and Ashby forms with the shared planner and explain blocked fields
- Record privacy-safe run summaries and preserve source cooldowns
- Preserve resume structure and support letter PDFs
- Guard application states and protect private artifacts
- Group previously stored duplicate positions
- Track canonical origin and group probable duplicate postings
- Improve review, material recovery, and application gates
- Recover scoring failures and add reviewed eligibility checks
- Plan application forms from Greenhouse questions and reviewed sources
- Allow explicit approval of reviewed material warnings
- Filter stale postings and enforce company application cooldown
- Record native terminal and Python compatibility validation
- Expand verified life-sciences sources and add watchlist validation

