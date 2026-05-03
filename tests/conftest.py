import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Point the SQLite DB at a temp file BEFORE app.database is imported anywhere,
# so init_db() (which runs at import time) doesn't write into the real data/ dir.
_TMP_DB_DIR = tempfile.mkdtemp(prefix="obsessed-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB_DIR}/trivia.db"

import app.database as _db  # noqa: E402

# app.database hard-codes its DB path; rebind the engine to the temp DB so
# tests don't touch (or require) the real data/ directory.
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

_test_db_path = f"{_TMP_DB_DIR}/trivia.db"
_db.DB_PATH = _test_db_path
_db.engine = create_engine(
    f"sqlite:///{_test_db_path}", connect_args={"check_same_thread": False}
)
_db.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_db.engine)
_db.Base.metadata.create_all(bind=_db.engine)
