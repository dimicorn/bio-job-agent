# Scheduled runs and alerts

Run `uv run bio-jobs watch` for one pass over all enabled sources. It uses the existing filters and versioned scoring cache, requires a reviewed profile, and alerts only for open, non-rejected jobs at or above `watch.min_score` (default: inherit `analyst.min_score`). Submitted applications do not alert. Existing eligible jobs can alert on the first watch run. Job title, description, requirements, location, compensation, arrangements, deadline and apply-link changes create a new alert version; provenance, posting timestamps, whitespace-only changes and changes to the candidate/scoring configuration alone do not. Returning to a previously alerted content version does not alert again.

Configure the optional `watch` block in `config.yaml`:

```yaml
watch:
  min_score: 70       # omit or use null to inherit analyst.min_score
  notify: true       # macOS desktop notifications; ignored on Linux
  telegram: false    # optional bot digest, off by default
  digest_dir: digests # relative to config.yaml, gitignored
```

Up to three matches produce individual macOS notifications; larger batches produce one summary. Every batch also produces a private Markdown digest in `digests/`. To enable Telegram, set `BIO_TELEGRAM_BOT_TOKEN` and `BIO_TELEGRAM_CHAT_ID` in gitignored `.env` and set `watch.telegram: true`. The bot must have access to the target chat. Only the standard library is used to send plain-text bot messages. Email and n8n hooks are deferred.

SQLite records each job/content version and its digest atomically before delivery. External delivery is attempted at most once: an OS/network error or a crash during delivery can lose that notification, but the local digest remains available and is restored on the next run if its file was not written. External delivery is not retried automatically, avoiding duplicate messages after uncertain outcomes. `stats` and `doctor` show per-source last complete success, retained last error and its time, consecutive failed/partial runs, and last complete snapshot count. Source cooldowns are not failures.

A vacancy closes after its origin is absent from **two complete successful snapshots**; another present origin keeps it open. Errors, rate limits, malformed/partial responses and cooldowns close nothing. Page watchers never expire job lists. Reappearing jobs reopen and reset their absence count. Application records, including submitted records, remain intact. Closed roles leave the scoring, review and auto-apply queues; the SQLite history remains available.

Rerun `./setup.sh` to optionally install or update a macOS LaunchAgent (default **no**). Choose one or two distinct local hours; it uses the repository template `launchd/ru.bio-job-agent.watch.plist`, sets PATH for `~/.local/bin` and `/opt/homebrew/bin`, writes `logs/watch.log`, and does not run at load time. The script prints the exact `launchctl unload` command. Declining leaves an existing schedule unchanged. Linux prints a ready-to-copy cron entry and never modifies crontab; create the indicated logs directory first. The project directory must remain at the installed path.

Schedules execute `uv run bio-jobs watch --scheduled`. Only `apply.mode: auto` adds the existing bounded autopilot queue pass; it still requires prior explicit approvals, current materials, active hours, score/limit gates and submission confirmation. Manual, assisted and dry-run modes only sync, score and alert. Watch does not generate approvals or materials. A database-specific kernel file lock prevents overlapping watch passes, including their auto-apply phase. Process exit/crash releases it automatically; a retained lock file is harmless. SQLite state and the installed schedule survive restarts; the next scheduled or manual run proceeds normally.
