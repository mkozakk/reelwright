from services.common import cutcache


def test_cache_key_is_stable_for_identical_inputs():
    profile = cutcache.profile_key("16:9", "1080p")
    a = cutcache.cache_key("raw/job1/source", 1.0, 3.5, 1.0, profile)
    b = cutcache.cache_key("raw/job1/source", 1.0, 3.5, 1.0, profile)
    assert a == b


def test_cache_key_changes_when_any_input_changes():
    profile = cutcache.profile_key("16:9", "1080p")
    base = cutcache.cache_key("raw/job1/source", 1.0, 3.5, 1.0, profile)

    assert cutcache.cache_key("raw/job1/other", 1.0, 3.5, 1.0, profile) != base
    assert cutcache.cache_key("raw/job1/source", 1.1, 3.5, 1.0, profile) != base
    assert cutcache.cache_key("raw/job1/source", 1.0, 3.6, 1.0, profile) != base
    assert cutcache.cache_key("raw/job1/source", 1.0, 3.5, 1.5, profile) != base

    other_profile = cutcache.profile_key("9:16", "1080p")
    assert cutcache.cache_key("raw/job1/source", 1.0, 3.5, 1.0, other_profile) != base
