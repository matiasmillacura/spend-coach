import os
import sys
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
os.environ["COACH_CHECKPOINT_DB"] = tempfile.mkstemp(suffix=".db")[1]
os.environ["COACH_ENGINE"] = "original"
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import app as app_module


@pytest.fixture()
def client():
    return app_module.app.test_client()
