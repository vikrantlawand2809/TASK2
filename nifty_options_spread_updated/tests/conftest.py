import pytest
from tests.helpers import make_env


@pytest.fixture
def env():
    return make_env()
