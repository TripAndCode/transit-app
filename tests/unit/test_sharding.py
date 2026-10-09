import pytest

from tests.sharding import assign_shards, shard_from_env


def test_every_file_lands_in_exactly_one_shard_and_the_loads_stay_even():
    files = {f"tests/test_{i}.py": tests for i, tests in enumerate([40, 35, 30, 12, 10, 9, 8, 5, 3, 1])}
    owner = assign_shards(files, 3)
    assert set(owner) == set(files)
    loads = [sum(files[path] for path, shard in owner.items() if shard == s) for s in range(3)]
    assert sum(loads) == sum(files.values())
    assert max(loads) - min(loads) <= max(files.values()) // 4


def test_the_assignment_does_not_depend_on_collection_order():
    files = {"tests/a.py": 3, "tests/b.py": 3, "tests/c.py": 1}
    reversed_files = dict(reversed(list(files.items())))
    assert assign_shards(files, 2) == assign_shards(reversed_files, 2)


def test_one_shard_takes_everything():
    assert set(assign_shards({"tests/a.py": 1, "tests/b.py": 2}, 1).values()) == {0}


def test_a_run_without_a_split_keeps_the_whole_suite():
    assert shard_from_env({}) is None
    assert shard_from_env({"CI_SHARD_COUNT": "1", "CI_SHARD_INDEX": "0"}) is None


def test_a_split_run_reads_its_share():
    assert shard_from_env({"CI_SHARD_COUNT": "3", "CI_SHARD_INDEX": "2"}) == (2, 3)


@pytest.mark.parametrize(
    "environ",
    [
        {"CI_SHARD_COUNT": "3"},
        {"CI_SHARD_COUNT": "3", "CI_SHARD_INDEX": "3"},
        {"CI_SHARD_COUNT": "3", "CI_SHARD_INDEX": "-1"},
    ],
)
def test_a_split_run_without_a_valid_index_is_refused(environ):
    with pytest.raises(ValueError):
        shard_from_env(environ)


def test_a_shard_count_below_one_is_refused():
    with pytest.raises(ValueError):
        assign_shards({"tests/a.py": 1}, 0)
