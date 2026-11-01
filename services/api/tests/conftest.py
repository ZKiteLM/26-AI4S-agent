"""Keep the test suite away from the developer's running database and storage."""

import os
import tempfile
from pathlib import Path

_test_dir = tempfile.TemporaryDirectory(prefix="ai4s-tests-")
_root = Path(_test_dir.name)
os.environ["DATABASE_URL"] = f"sqlite:///{_root / 'research.db'}"
os.environ["S3_ENDPOINT"] = ""
os.environ["LOCAL_STORAGE"] = str(_root / "storage")
os.environ["QDRANT_URL"] = f"local://{_root / 'qdrant'}"
os.environ["PARSER_VECTOR_ENHANCE"] = "0"
