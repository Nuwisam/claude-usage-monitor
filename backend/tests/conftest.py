"""Environment for the tests.

Set HERE and not on the command line, because `app/config.py` builds `Settings()` at
module level — a value that arrives later than the first `app.*` import will not be
noticed. A directory's conftest.py is loaded before the tests in it are collected,
that is, before that import.

A plain assignment, NOT `setdefault`: a stray AUTH_MODE or ALLOWED_EMAILS in a developer's
shell would then decide what this suite checks at all, and the result would drift between
machines with no trace in the repository.
"""
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["INGEST_TOKENS"] = "t:m"
os.environ["ALLOWED_EMAILS"] = "a@b.pl"
# The suite's default mode. Tests that check the modes themselves patch `settings`
# individually — see tests/test_auth_modes.py.
os.environ["AUTH_MODE"] = "none"
