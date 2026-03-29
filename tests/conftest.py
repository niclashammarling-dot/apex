"""
Test configuration — redirects all DB access to a temporary file.

Must use pytest_configure (not a fixture) so the path is set before
test modules are imported. test_wallet.py calls init_db() and imports
DB_PATH at module level during collection, both of which must see the
temp path rather than the production data/apex.db.
"""
import tempfile
from pathlib import Path


def pytest_configure(config):
    """Redirect DB to a temp file before any test modules are imported."""
    import backend.db as db_module
    tmp = tempfile.mkdtemp(prefix="apex_test_")
    db_module.DB_PATH = Path(tmp) / "apex_test.db"
