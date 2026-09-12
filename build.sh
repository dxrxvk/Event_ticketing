#!/usr/bin/env bash
# Render build command. Run from the repo root.

# Stop at the first failure. Without this a failed migrate still exits 0 and Render
# deploys an app whose schema does not match its code.
set -o errexit

# uv is not on Render's Python image, but installing it keeps uv.lock as the single
# source of truth. An exported requirements.txt drifts the moment someone forgets to
# regenerate it.
pip install uv

# --frozen fails if uv.lock is stale rather than silently resolving different versions
# than were tested locally.
uv sync --frozen

# WhiteNoise serves from STATIC_ROOT; nothing is there until this runs.
uv run python manage.py collectstatic --no-input

# Includes 0002_seed_event_settings, so the EventSettings row exists on a fresh
# production database without anyone remembering to create it.
uv run python manage.py migrate
