import sqlite3
import pytest

from tecan_reader.project_index import _count as pi_count
from tecan_reader.pattern_library import _count as pl_count

@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE dummy (id INTEGER)")
    conn.execute("INSERT INTO dummy VALUES (1)")
    conn.execute("INSERT INTO dummy VALUES (2)")
    yield conn
    conn.close()

def test_project_index_count_valid(db_conn):
    assert pi_count(db_conn, "dummy") == 2

def test_pattern_library_count_valid(db_conn):
    assert pl_count(db_conn, "dummy") == 2

def test_project_index_count_sqli(db_conn):
    # Attempt SQL injection: close table name and comment out rest
    malicious_table = 'dummy" --'

    # In an unpatched version, f'SELECT COUNT(*) AS count FROM {table}'
    # would evaluate to `SELECT COUNT(*) AS count FROM dummy" --` which is invalid
    # if not escaped, but let's try a different one.

    # If the unescaped code is: f"SELECT COUNT(*) AS count FROM {table}"
    # and table is "dummy; DROP TABLE dummy; --", this would execute multiple statements if executescript were used.
    # With execute(), it would raise Warning/Error.

    # Because we escape with "", SQLite treats the malicious table name as exactly the literal table name
    # e.g. it looks for a table literally named `dummy; DROP TABLE dummy; --`
    # which does not exist, and should raise an OperationalError about no such table.

    malicious_table = "dummy; DROP TABLE dummy; --"
    with pytest.raises(sqlite3.OperationalError) as exc_info:
        pi_count(db_conn, malicious_table)
    assert "no such table" in str(exc_info.value)

    # Also verify it handles quotes correctly
    malicious_table = 'dummy" OR 1=1; --'
    with pytest.raises(sqlite3.OperationalError) as exc_info:
        pi_count(db_conn, malicious_table)
    assert "no such table" in str(exc_info.value)

def test_pattern_library_count_sqli(db_conn):
    malicious_table = "dummy; DROP TABLE dummy; --"
    with pytest.raises(sqlite3.OperationalError) as exc_info:
        pl_count(db_conn, malicious_table)
    assert "no such table" in str(exc_info.value)

    malicious_table = 'dummy" OR 1=1; --'
    with pytest.raises(sqlite3.OperationalError) as exc_info:
        pl_count(db_conn, malicious_table)
    assert "no such table" in str(exc_info.value)
