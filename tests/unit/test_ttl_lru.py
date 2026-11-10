"""pipeline.cache.TtlLru: a bounded, expiring store for synchronous callers."""

from pipeline.cache import TtlLru, clear_all


def test_an_entry_is_served_until_its_ttl_passes(cache_clock):
    cache: TtlLru[str, int] = TtlLru(maxsize=4, ttl_seconds=10)
    cache.put("a", 1, cache.generation)
    cache_clock.now += 10
    assert cache.get("a") == 1
    cache_clock.now += 1
    assert cache.get("a") is None


def test_the_least_recently_used_entry_goes_first():
    cache: TtlLru[str, int] = TtlLru(maxsize=2, ttl_seconds=60)
    cache.put("a", 1, cache.generation)
    cache.put("b", 2, cache.generation)
    assert cache.get("a") == 1
    cache.put("c", 3, cache.generation)
    assert (cache.get("a"), cache.get("b"), cache.get("c")) == (1, None, 3)


def test_a_value_read_before_a_clear_is_not_stored_after_it():
    cache: TtlLru[str, int] = TtlLru(maxsize=4, ttl_seconds=60)
    before = cache.generation
    cache.clear()
    cache.put("a", 1, before)
    assert cache.get("a") is None
    cache.put("a", 2, cache.generation)
    assert cache.get("a") == 2


def test_clear_all_clears_it():
    cache: TtlLru[str, int] = TtlLru(maxsize=4, ttl_seconds=60)
    cache.put("a", 1, cache.generation)
    clear_all()
    assert cache.get("a") is None
