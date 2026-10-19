from __future__ import annotations

import pytest

from motionlang.testing import BASE, make_doc  # noqa: F401


@pytest.fixture
def doc_factory():
    return make_doc
