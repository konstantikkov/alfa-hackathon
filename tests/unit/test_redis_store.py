import fakeredis

from app.storage.redis_store import RedisMappingStore


def make_store() -> RedisMappingStore:
    return RedisMappingStore(fakeredis.FakeStrictRedis())


def test_save_if_absent_then_get():
    store = make_store()
    created = store.save_if_absent("p1", {"a": 1}, ttl_seconds=60)
    assert created is True
    assert store.get("p1") == {"a": 1}


def test_save_if_absent_does_not_overwrite_existing():
    store = make_store()
    store.save_if_absent("p1", {"a": 1}, ttl_seconds=60)
    created_again = store.save_if_absent("p1", {"a": 2}, ttl_seconds=60)
    assert created_again is False
    assert store.get("p1") == {"a": 1}


def test_update_overwrites():
    store = make_store()
    store.save_if_absent("p1", {"a": 1}, ttl_seconds=60)
    store.update("p1", {"a": 2}, ttl_seconds=60)
    assert store.get("p1") == {"a": 2}


def test_get_missing_returns_none():
    store = make_store()
    assert store.get("missing") is None


def test_delete_removes_record():
    store = make_store()
    store.save_if_absent("p1", {"a": 1}, ttl_seconds=60)
    store.delete("p1")
    assert store.get("p1") is None


def test_ping():
    store = make_store()
    assert store.ping() is True
