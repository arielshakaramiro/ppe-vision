import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="ppe-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["LLM_PROVIDER"] = "offline"
os.environ["RUN_PIPELINE"] = "0"
os.environ["COOKIE_SECURE"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
