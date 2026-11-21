# Applications: materials, answers and browser modes

Reference detail for preparing and submitting applications. See the [README](../README.md) for the daily workflow.

## Preparing materials

Preparation uses your reviewed candidate profile and its matching resume hash. Run
`extract-profile`, review the facts and set `reviewed: true` before generating materials.
Job preferences never count as qualifications. Prepare a scored job in one step:

```bash
uv run bio-jobs prepare 42              # everything missing or stale, then the open questions
uv run bio-jobs review                  # or simply press a on the job: it prepares first
```

`prepare` (and `a` in review) writes the cover letter, tailored resume, resume PDF with its
ATS check and the screening draft, but only for pieces that are missing or stale; current
materials stay as they are, with no new versions. It then shows the letter with each claim
and its cited facts, plus any warnings. Before any browser opens it reads the job's
application form (see [Form questions at preparation](#form-questions-at-preparation)),
then asks every required question and every optional one the answer bank cannot answer,
listing the form's options. Without an interactive terminal it prepares everything it can,
lists the unanswered questions and prints the command to run in a terminal. A material file
you edited is never replaced: preparation stops with the `import` command to run first.

### Form questions at preparation

Preparation reads the form with GET requests only, through the same robots.txt checks and
per-host pacing as discovery; nothing is posted or submitted:

| ATS | Source |
| --- | --- |
| Greenhouse | public job-board API, `…/jobs/<id>?questions=true`; the hosted form's Country selector next to Phone is added as a required question |
| Lever | the public `…/apply` form page |
| Workable | the public form API, `apply.workable.com/api/v1/jobs/<shortcode>/form` |
| Ashby | not read: its form API is under `jobs.ashbyhq.com/api/`, which robots.txt disallows |

The questions are stored for the job, so `answers` and approval know them. Your answers
are saved to `answers.yaml` and reused by later jobs with the same questions, exactly as
`answers` does. Declarations, consent and other sensitive questions are asked too, marked
as only yours to answer; the LLM never answers them. Files (resume, cover letter) and
optional equal-opportunity surveys are not asked.

Standard contact fields are filled from your reviewed profile and resume, never from the
LLM: name, email, phone, city, LinkedIn, GitHub and website from the profile, or from the
resume when it states exactly one email, phone (a `Phone:` line or the contact header), LinkedIn or
GitHub link. Country comes from the profile's `city` when it ends in a country
(`London, UK`) or from a stored `Country` answer. A contact field with no such value is
asked like any other question; nothing is invented.

If the form cannot be read (network error, HTTP error, robots.txt, Ashby or an unsupported
site), preparation says so plainly and carries on; the browser fill reads the live form as
before and queues any new questions for `bio-jobs answers --job <id>`. Required questions
still unanswered become warnings on the screening draft, so approval asks you to accept
them by typing `approve` rather than pinning them silently.

The single commands remain for redoing one piece:

```bash
uv run bio-jobs letters                 # batches jobs at analyst.min_score
uv run bio-jobs letters --job 42 --force # keep a new version of an existing letter
uv run bio-jobs resume 42               # tailored, editable Markdown
uv run bio-jobs pdf 42                  # Chromium PDF plus pdfminer ATS text check
uv run bio-jobs letter-pdf 42           # optional PDF of the saved cover letter
uv run bio-jobs diff 42 --source        # compare the current resume file to resume.md
uv run bio-jobs answers --job 42 --show # prepare screening draft, including an empty draft
uv run bio-jobs review --status all
uv run bio-jobs diff 42                 # latest two versions, all material types
```

Resume preparation preserves the source Markdown and its chronological roles, dates,
contact details and education exactly. It does not reorder ambiguous Markdown blocks or
invent experience. The command shows a diff against `resume.md`; `diff 42 --source`
compares the current editable file, including unsaved edits. Run `pdf 42` to capture
edits in a new version and rebuild it. The ATS check verifies the name, email, section
order and text ligatures. Missing contact details require your edit.
`./setup.sh` installs Chromium; `doctor` reports when it is missing. Install it manually
with `uv run playwright install chromium` if needed.

Letters are written in the vacancy's language through the configured LLM. Set optional
`analyst.letter_style` and `analyst.letter_length` in config.yaml to guide the draft;
the default length is 150–250 words. `letter-pdf` renders the saved letter for upload. Each generated
candidate claim cites reviewed fact IDs. A deterministic check looks for unsupported
numbers, technologies, employers, degrees, names and invalid/missing fact references.
Job text can support statements about the employer, but job requirements do not establish
candidate experience. Each candidate claim is checked against its cited facts as well.
A failing draft receives one guided rewrite. If it still fails, or the rewrite fails,
the draft is saved with visible review warnings. These lexical checks are conservative;
read the letter and its cited facts before approving. They do not prove the meaning of
a paraphrase. Review each warning and correct or rewrite any unsupported claim. Warnings on your own edits remain visible as advisories.

Every letter, resume and screening draft has a database snapshot and an editable file
under `materials/<job>/`, linked to the job content and reviewed profile version. PDF
builds also record an integrity hash and ATS warnings. Changed profile/job inputs make
old materials stale. New edits or generations return the application to `awaiting_review`.
`diff 42 --kind letter` shows a readable changelog between letter versions. The database
retains earlier content even if a material file is edited directly.

In review, the letter display shows each cited claim with the reviewed fact and its verbatim CV evidence. Compare the sentence against those excerpts; missing cited facts are flagged. A user-edited letter has no generated claim citations, so verify its claims directly against your CV. The `v` action overrides the score; provenance is displayed with the letter in the review screen.

Externally edited material must be imported before approval: run `bio-jobs import <job> --kind letter` (or `resume` / `answers`), inspect the diff and type `import`. This creates a new version and requires review; a new resume needs a fresh PDF. Answers must agree with the answer bank: update the bank and regenerate the snapshot if they differ. Explicit preparation requests respect score thresholds; run `bio-jobs analyze` or use `review --all-scores` and `v` to record an override with a reason.

Profile extraction leaves a missing or unsupported header name empty and lists it as an uncertainty. Review the extracted name and evidence along with all facts. An existing person-edited name is retained. Doctor warns about an empty or example name; set your name in the candidate profile before generating letters or tailored CVs or filling forms.

## Screening answers bank

Preparation stores the questions it reads from the form, and browser filling queues any
others it finds, for `answers`; you can also register them manually:

```bash
uv run bio-jobs answers --job 42 --add 'Do you require visa sponsorship?' --option Yes --option No --show
uv run bio-jobs answers                  # interactively fill unanswered questions
```

`answers.yaml` is a private, editable answer bank; start with `answers.example.yaml`.
Question matching ignores case, required markers (`*`, Lever's `✱`) and trailing
punctuation. Stored answers are matched to listed options by normalised text. For
non-sensitive questions such as office or location preferences, an answer written in your
own words also counts when exactly one option contains it (or it contains exactly one
option); an answer that fits several options, such as `London` against both "based in the
UK… London" and "willing to relocate… London", stays `needs_human` with that reason.
Sensitive questions need the exact option text. Answers are
reused across employers when their options match. An `Employer: Question` key overrides
a shared answer. If a shared answer does not fit another employer's options, the interactive
command saves the replacement for that employer. Enter option numbers separated by commas
for multiple selections; Enter leaves the question unanswered. Sensitive declarations and
unsupported questions stay `needs_human` until you supply them.
Screening draft snapshots retain the actual questions, selected answers and their source.

## Review keys and approval

Review keeps `o` open, `s` save, `r` reject, `l` later, `v` score override and `q` quit.
Additional keys are:

| Key | Action |
| --- | --- |
| `e` | Edit the letter with `$VISUAL`, `$EDITOR`, or `nano`; editor arguments such as `code --wait` work. Saves a checked new version. |
| `w` | Rewrite the letter with your instruction, preserving factual checks. |
| `d` | Show the material diff. |
| `a` | Prepare any missing or stale materials (as `prepare` does), show anything new for confirmation, then approve and pin the current letter, CV PDF and answer versions. Manual mode opens the apply link; assisted mode offers **f** to fill immediately, leaving Submit to you; auto mode prints the `apply` command. |
| `t` | Record `submitted` only after you type `submitted` to confirm you completed the application yourself. |
| `m` | Set a preparation state, including `blocked`, `manual_required` or `withdrawn`. |

Approval requires current, unchanged letter/resume/answer files, a checked resume PDF,
explicit acknowledgement of any material or ATS warnings. When `a` had to prepare anything,
review shows the new materials and asks you to type `approve` before recording approval.
Review lists warnings by material; type `approve` to accept them or anything else to cancel. The approval and application event record the acknowledged warnings. Edited material files, cooldowns, and score and limit gates remain blockers; missing or stale materials are prepared first. It only marks the
job approved; browser execution is a separate `apply` command. Saving a review decision
also initializes the application state to `saved`. States are `saved`, `preparing`,
`awaiting_review`, `approved`, `blocked`, `manual_required`, `withdrawn`, `submitting`,
`unknown_result` and `submitted`. Submitted, unknown-result and withdrawn states cannot be changed through ordinary state or preparation commands. After verifying an uncertain result, use the explicit submitted confirmation command to record a confirmed submission. Preparing unchanged materials preserves approval; changed content requires review again. Use `list --application-state awaiting_review`, `stats`, or
`state 42 manual_required` to inspect or update preparation. `approved` and `submitted`
require their dedicated review actions.

## Apply settings and browser modes

The setup wizard persists these settings in your local `config.yaml`:

```yaml
apply:
  mode: manual       # manual | assisted | auto
  auto_consent: false
  auto_llm_answers: false
  daily_limit: 5
  max_per_run: 5
  min_score: 80
  active_hours: [8, 23]
  answers_file: answers.yaml
  profile_dir: data/browser-profile
  audit_dir: debug/apply
  submit_wait_minutes: 10
  confirmation_timeout_seconds: 45
  action_seconds: 0.3
```

```bash
uv run bio-jobs apply 42           # one previously approved job
uv run bio-jobs apply              # approved queue, bounded by max_per_run
uv run bio-jobs autopilot          # auto mode: sync/score, then consume approved queue
uv run bio-jobs run --no-scan       # alias; use stored approved queue only
```

`active_hours` restricts unattended auto application; manual and assisted actions are available at any hour. `manual` opens the link for completion yourself. `assisted` fills and verifies the form,
then waits up to `submit_wait_minutes` for you to press Submit and for an acknowledgement.
`auto` presses Submit once, then waits up to `confirmation_timeout_seconds` for an
acknowledgement. Both browser modes support Greenhouse (including embedded forms), Lever,
Workable and Ashby when its board is enabled. Standard HTML controls, common React-select
controls, Workable `data-ui` choice groups and listboxes (the optional-cookie banner is
declined), Ashby field paths, yes/no buttons and location menus, and Lever question labels,
custom card questions and resume upload parsing are supported. Location suggestions are
typed by city and must narrow to one suggestion by the profile city's region or country
(names and codes such as `United Kingdom`/`GBR` are equivalent); otherwise the job stops for
you to choose. Workable's IP-prefilled Address is replaced with the profile city.
Changed or unsupported widgets stop for manual handling. No live employer submissions are part of testing.

## Question planning and blocked fields

Greenhouse questions are fetched from its public job-board API with robots checks and
request pacing, then matched to the embedded application form. A mismatch stops the job.
The shared planner uses profile contacts and links, the approved CV and cover letter, then
employer-specific or global `answers.yaml` entries. A contact field (phone, country, links)
with none of these values is queued for you; it is never sent to the LLM. Optional single-option consent can be
enabled with `auto_consent`. It then asks the configured LLM once for remaining questions,
using the approved CV, reviewed facts and stored answers; unsupported questions are queued.
Options must match the form. Visa, work authorization, legal declarations, demographics and
assessments are never answered by the LLM: only stored answers may fill them, and optional
demographic questions remain blank.

The plan prints each answer and its source before filling, prints each blocked field as
`Blocked: <question> [reason]` (sensitive with no stored answer, `needs_human`, or option
not matched with the stored value), and records sources and reasons in the audit log. In assisted mode, review LLM answers in the browser before pressing Submit. In auto
mode, a required free-text LLM answer stops the application unless `auto_llm_answers` is
explicitly enabled. After confirmation, use `bio-jobs answers` to save any accepted LLM
answers yourself; they are never saved automatically. Unknown required questions,
mismatches, sign-in, MFA and visible CAPTCHA challenges stop the job as `manual_required`.
Invisible background CAPTCHA widgets do not block filling. If a visible challenge appears after
an automatic submission, the agent notifies you and waits for confirmation while you handle it.
The browser stays open for a person; no challenge is bypassed. In assisted mode an unanswered
single-option legal consent is left for the person after the other supported fields are filled;
the application stops for manual completion. Unknown optional questions are queued and left
blank; an unapproved prefilled value stops the job. After answering a newly discovered
question, rebuild `answers` and approve again.

## Submission safeguards

Browser modes recheck approval, material integrity, the higher of analyst/apply score
thresholds and local active hours before submission. Daily slots are reserved atomically
across processes; attempts count conservatively against the local-day limit, including
manual stops and uncertain results. `max_per_run` bounds attempts. A missing acknowledgement
becomes `unknown_result`, never an automatic retry. A crash during an attempt, a human
handoff or a challenge after clicking also leaves a durable retry barrier, even if someone
later changes the application state. Reconcile these applications on the employer site;
review → `t` can record a submission you personally verified. Automatic retries are never
unlocked by a state change. A URL containing “confirmation” and a click alone are insufficient.

`apply.company_cooldown_days` defaults to 30 (0 disables it). Submitted, submitting and uncertain applications block another application to the same normalized employer until the cooldown expires, across sources and application modes. Approval and application are blocked during cooldown. Each apply pass selects at most the highest-scoring eligible approved job per employer.

## Browser profile and audit trail

Ordinary Chromium uses the gitignored persistent profile; no stealth plugins or fingerprint
changes are used. Filled-form and confirmation screenshots are saved under the gitignored
`debug/apply` directory. Application events and attempt records in SQLite retain actions,
version IDs, the approved answer-bank snapshot, verification hashes, reasons and confirmation evidence. Screenshots contain personal data; keep
custom profile/audit locations private and ignored too. On a stop or timeout, the CLI
disconnects and leaves Chromium open. Close the tab after manual handling. The next attempt
opens a fresh tab, preserving previous handoffs. Scheduling and notifications are handled by the separate `watch` command described in the README.

## Material tests

Offline material tests mock the LLM. A real PDF test runs when Chromium is available and
skips cleanly otherwise. To spend an opt-in request on a synthetic letter (no private data):

```bash
BIO_LIVE_LETTER=1 uv run pytest tests/integration/test_materials.py -k live_synthetic_letter -m live --live -q -s
```
