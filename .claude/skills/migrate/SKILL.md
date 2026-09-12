---
name: migrate
description: Generate, apply, verify and commit Django migrations in this repo. Use whenever tickets/models.py changes, whenever a migration needs creating or applying, after adding or altering a model field, or when the user asks to migrate. Always ends in a git commit.
---

# Migrate and commit

Run these in order. If any step fails, stop and report it — never commit a migration that
did not apply cleanly.

## 1. Generate

```sh
uv run python manage.py makemigrations tickets
```

- "No changes detected" means there is nothing to do. Stop here.
- **If it prompts for a default value, do not answer the prompt.** That happens when a
  non-nullable field is added to a table that already has rows. Abort, then fix it in
  `tickets/models.py` by giving the field a `default=` or `null=True`, and run again.
  Answering interactively bakes a one-off value into the migration and will not reproduce
  on the production database.
- Read the generated file before moving on. Auto-generated is not automatically correct —
  check especially that a rename was detected as a rename rather than a drop plus an add,
  which would destroy data.

## 2. Apply

```sh
uv run python manage.py migrate
```

## 3. Verify

Exercise whatever changed before committing. A quick shell round-trip is usually enough:

```sh
uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
django.setup()
from tickets.models import EventSettings, Booking, Guest, seats_taken
# ... create, read back, assert the new behaviour, then clean up
"
```

Also run the test suite if any tests exist yet:

```sh
uv run python manage.py test tickets
```

## 4. Commit

Stage the model change and its migrations together — a migration separated from the model
it came from is not reviewable:

```sh
git add tickets/models.py tickets/migrations/
git commit
```

- Never `git add db.sqlite3`. It is gitignored, it is local scratch data, and on this
  project it holds real people's phone numbers.
- Subject line: what the schema now expresses, not "add migration". Good:
  `Add Booking, Guest and EventSettings models`. Bad: `migration 0003`.
- If the change overrules something in `event_ticketing.md`, say so in the body — that doc
  is still the nominal build plan and the divergence needs to be findable later.
- End the commit message with:

```
Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```

## Notes for this repo

- Everything runs through `uv run`; there is no activated virtualenv.
- The app is `tickets` (renamed from `mcm`). `config/` holds settings and urls.
- `EventSettings` is a singleton seeded by `0002_seed_event_settings`. If you ever add a
  field to it, give it a default — the production row already exists and will not be
  recreated.
- The database is local SQLite today and moves to Neon Postgres at step 2.5 of the
  amended build order. After that move, re-run migrations against Neon as well.
