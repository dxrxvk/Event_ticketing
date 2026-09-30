"""Settings that only go wrong on a fresh clone, where nobody is looking.

Settings are read once per process, so each case runs in a new interpreter rather than
reloading the module under the test runner's feet.
"""

import os
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase

PRINT_ENGINE = (
    'import django; django.setup(); '
    'from django.conf import settings; '
    'print(settings.DATABASES["default"]["ENGINE"])'
)


def engine_for(database_url):
    env = {**os.environ, 'DATABASE_URL': database_url, 'DJANGO_SETTINGS_MODULE': 'config.settings'}
    result = subprocess.run(
        [sys.executable, '-c', PRINT_ENGINE],
        cwd=settings.BASE_DIR, env=env, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


class DatabaseUrlTests(SimpleTestCase):
    # `DATABASE_URL=` in .env sets the variable to '', and dj_database_url only falls
    # back to its default when the variable is absent. That produced a config with no
    # ENGINE, and a stray space failed to parse at all.
    def test_blank_falls_back_to_sqlite(self):
        self.assertEqual(engine_for(''), 'django.db.backends.sqlite3')

    def test_whitespace_falls_back_to_sqlite(self):
        self.assertEqual(engine_for('   '), 'django.db.backends.sqlite3')

    def test_a_postgres_url_is_still_used(self):
        self.assertEqual(
            engine_for('postgres://user:pass@example.invalid/neondb?sslmode=require'),
            'django.db.backends.postgresql',
        )
