# Ingestion data handoff: Member A → Member B

This guide transfers the collected PostgreSQL dataset to the teammate building
features and the win-probability model. GitHub shares the code; the database on
Member A's computer must be exported and shared separately.

Run all commands below from the **repository root**, not this directory or the
interactive `psql` prompt. The examples use the checked-in Compose service
`db`, PostgreSQL user `rift`, and source database `rift`. If your collection
used a different DATABASE_URL, export that actual database instead.

## Member A: prepare and export

### 1. Finish collection and check the dataset

Wait until the collector finishes; avoid changing the dataset during handoff.

```bash
python -m src.ingestion status --region americas
git rev-parse HEAD
```

Record the complete-match count, table counts, and commit SHA. Do not assume
that finishing a command means 100 complete matches were accepted: review
the statuses and any failed or excluded records.

Check whether the synthetic test match is present:

```bash
docker compose exec -T db psql -U rift -d rift -c \
  "SELECT match_id FROM matches WHERE match_id = 'DEMO_1';"
```

If present, identify it in the handoff note and have Member B exclude it from
analysis and training. Do not count it toward the real-match target.

### 2. Export the full database

The custom-format archive includes schema, tables, view definitions, raw JSON
responses, normalized data, and ingestion metadata. It does not export your
local .env or Riot API key.

```bash
mkdir -p data/processed
docker compose exec -T db pg_dump -U rift -d rift -Fc \
  > data/processed/rift_handoff.dump.tmp && \
  mv data/processed/rift_handoff.dump.tmp data/processed/rift_handoff.dump
```

Only share `rift_handoff.dump` after the command succeeds. Using a temporary
file avoids replacing an earlier good backup when export fails.

Check that PostgreSQL recognizes the archive:

```bash
docker compose exec -T db pg_restore --list \
  < data/processed/rift_handoff.dump
```

This checks the archive's table of contents; a successful restore and matching
counts on Member B's machine are the final verification.

Optionally create a checksum on macOS:

```bash
shasum -a 256 data/processed/rift_handoff.dump \
  > data/processed/rift_handoff.dump.sha256
```

On Linux use `sha256sum` instead of `shasum -a 256`.

### 3. Share the handoff package

Send through the team's private shared drive:

- `rift_handoff.dump`
- The checksum file, if created
- Your status output and a completed handoff note (template below)
- The repository URL, branch, and commit SHA used to produce the dataset

The database archive contains raw player identifiers. Share it within the team;
do not commit it to the public repository. Files under `data/processed/` are
already ignored by Git. Do not include your .env or API key.

## Member B: restore and verify

### 1. Get the matching code and dependencies

If the repository has not been cloned:

```bash
git clone https://github.com/boxiongdu/lol-winning-probability.git
cd lol-winning-probability
```

Then obtain the ingestion implementation:

```bash
git fetch origin
git switch feature/data-ingestion
git pull --ff-only
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Compare `git rev-parse HEAD` with Member A's handoff SHA. For the exact snapshot,
use `git switch --detach <handoff-commit-sha>` for verification.

For development on `feature/modeling`, incorporate the reviewed ingestion work
through `dev` once merged. Until then, merge `origin/feature/data-ingestion`
into the modeling branch if the team agrees to that dependency.

Windows PowerShell activation: `.venv\Scripts\Activate.ps1`. The multiline
shell commands and binary redirections in this guide target macOS/Linux or WSL.

### 2. Start PostgreSQL and create an empty destination

Place the shared archive at `data/processed/rift_handoff.dump`, then run:

```bash
docker compose up -d --wait db
docker compose exec -T db createdb -U rift rift_handoff
```

This creates a separate database, preserving any existing `rift` dataset.
If `rift_handoff` already exists, stop and choose a new empty database name
rather than restoring over existing tables. Adjust subsequent commands to match.

### 3. Restore the archive

```bash
docker compose exec -T db \
  pg_restore -U rift -d rift_handoff \
  --no-owner --no-privileges --single-transaction --exit-on-error \
  < data/processed/rift_handoff.dump
```

The archive already includes the schema. **Do not run init-db or load-fixture
against the destination before restoring.** The single transaction prevents
a failed restore from leaving a partially restored dataset.

If you created a checksum on macOS, verify it before restoring with:

```bash
shasum -a 256 -c data/processed/rift_handoff.dump.sha256
```

For a Linux-generated checksum use `sha256sum -c`.

### 4. Configure the restored connection and compare counts

Create a repository-root .env if you do not already have one, using
.env.example as the template. Set:

```dotenv
DATABASE_URL=postgresql://rift:rift_local@localhost:5432/rift_handoff
RIOT_REGION=americas
```

Use the actual local PostgreSQL password if you changed it. An exported
DATABASE_URL overrides .env; ensure it points to the restored database.
Member B does not need a Riot API key for status checks, SQL analysis, or modeling.

```bash
python -m src.ingestion status --region americas
```

Compare the output with Member A's saved counts. Investigate discrepancies
before building features. Inspect example records:

```bash
docker compose exec -T db psql -U rift -d rift_handoff -c \
  "SELECT * FROM team_frame_states ORDER BY match_id, timestamp_ms, team_id LIMIT 10;"
```

## What Member B should read

| Table or view | Purpose |
| --- | --- |
| matches | Match ID, start time, patch, queue, duration and final blue-team result |
| participants | Participant-to-team mapping and champion ID |
| participant_frames | Timestamped positions, gold, XP, level and minion counts |
| events | Kills, objectives and other events, with original event JSON |
| team_frame_states | Nonterminal team aggregates for gold, XP, level and CS |
| raw_responses | Original successful match/timeline responses for replay and diagnosis |
| collection_matches | Complete, pending, excluded and failed collection statuses |
| ingestion_runs | Collection/rebuild status and metrics |

See the [data dictionary](../../docs/data_dictionary.md),
[shared data contract](../../docs/data_contract.md), and
[Member A runbook](../../docs/member_a_week8.md).

The team-frame view is an initial handoff, not the finished model feature table.
Member B still needs to:

- Join timestamps to final outcomes to construct labels.
- Aggregate events only through each snapshot's actual timestamp.
- Use `credited_team_id` for event attribution; filter building and monster
  subtypes to distinguish towers, inhibitors, dragons, and Barons.
- Keep final winner and total match duration out of prediction inputs.
- Split whole matches chronologically, keeping every frame of a match in one split.
- Exclude DEMO_1 if present and document the dataset's sampling limitations.

## Handoff note template

Fill in actual values; no collection result is assumed by this README.

| Item | Recorded value |
| --- | --- |
| Code branch / commit SHA | |
| Export date and time / timezone | |
| Region / queue / map | |
| Date window and patch coverage | |
| Seed selection method and number of seed players | |
| Collection options / exclusion policy | |
| Real complete matches | |
| Pending / failed / excluded matches | |
| Raw responses / participants / frames / events | |
| Synthetic DEMO_1 present? | |
| Archive filename / SHA256 | |
| Known limitations or validation issues | |

After restoration, Member B confirms the checksum (if supplied), matching
counts, and readable sample rows. Keep the archive as a versioned handoff
snapshot so subsequent work can reproduce the same dataset.

## Week 9 additions

Run `python -m src.ingestion init-db` to apply the additive storage migration.
Generate the champion coverage handoff with:

```bash
python -m src.ingestion audit --region americas > data/processed/champion_coverage.json
```

Create data/processed first if needed. Optional `--patch 16.22` restricts the audit
and collection/rebuild policy; use the actual agreed patch. See
[Member A Week 9](../../docs/member_a_week9.md) for report interpretation, expanded
collection, versioned migrations and the session persistence interface for C/D.
Full pg_dump backups now include session/transition records and schema_migrations.
Restore them with matching code into an empty database using the procedure above.
