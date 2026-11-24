import pytest

from kassivo.db import connect


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()
