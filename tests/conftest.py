import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aml_agent.data_store import DataStore  # noqa: E402


@pytest.fixture(scope="session")
def store():
    return DataStore()
