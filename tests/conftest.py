"""Every test run uses its own throwaway NOTAM database (never data/notams.db nor a real DATABASE_URL)."""
import os
import tempfile


def pytest_configure(config):
    os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='notam-tests-')}/notams.db"
