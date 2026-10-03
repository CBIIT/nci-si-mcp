"""CI's selftest jobs share the self-tests out between them, each test to one job."""

import pytest
from conftest import shard

NODEIDS = [f"selftests/test_x.py::test_{number}" for number in range(7)]


def test_the_shards_hold_every_test_once_and_evenly():
    shards = [shard(NODEIDS, f"{index}/3") for index in (1, 2, 3)]

    assert set().union(*shards) == set(NODEIDS)
    assert sorted(map(len, shards)) == [2, 2, 3]


@pytest.mark.parametrize("spec", ["0/2", "3/2"])
def test_a_shard_outside_the_count_is_refused(spec):
    with pytest.raises(ValueError, match="the shard is one of 1 to 2"):
        shard(NODEIDS, spec)
