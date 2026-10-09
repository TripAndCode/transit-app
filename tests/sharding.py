"""Splitting the suite across CI jobs.

CI runs the backend suite as several jobs at once, each with its own Postgres
and ClickHouse, and each keeps only its share of the collected tests. Files
stay whole, so a module's fixtures set up once per job, and every job
collects the same suite, so the shares cover it exactly once.
"""

from collections.abc import Mapping


def assign_shards(tests_per_file: Mapping[str, int], count: int) -> dict[str, int]:
    """The shard each file runs in: largest file first, each to the shard
    holding the fewest tests so far. Ties break on the file path and then the
    lowest shard, so every job computes the same assignment."""
    if count < 1:
        raise ValueError(f"shard count must be at least 1, got {count}")
    loads = [0] * count
    owner: dict[str, int] = {}
    for path, tests in sorted(tests_per_file.items(), key=lambda entry: (-entry[1], entry[0])):
        shard = loads.index(min(loads))
        owner[path] = shard
        loads[shard] += tests
    return owner


def shard_from_env(environ: Mapping[str, str]) -> tuple[int, int] | None:
    """`(index, count)` from `CI_SHARD_INDEX`/`CI_SHARD_COUNT`, or None when
    the run is not split. A split run without a valid index is refused: a
    silently empty or duplicated share would read as a pass."""
    count = int(environ.get("CI_SHARD_COUNT", "1"))
    if count <= 1:
        return None
    raw = environ.get("CI_SHARD_INDEX")
    if raw is None:
        raise ValueError("CI_SHARD_COUNT is set above 1 without CI_SHARD_INDEX")
    index = int(raw)
    if not 0 <= index < count:
        raise ValueError(f"CI_SHARD_INDEX {index} is outside 0..{count - 1}")
    return index, count
