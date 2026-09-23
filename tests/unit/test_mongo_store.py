from __future__ import annotations

import threading

import mongomock

from app.storage.mongo_store import MongoMappingRepository


def make_repo() -> MongoMappingRepository:
    client = mongomock.MongoClient()
    return MongoMappingRepository(client["pii"]["mappings"])


def test_save_if_absent_creates_and_reads_back():
    repo = make_repo()
    assert repo.save_if_absent("p1", {"a": 1}, 60) is True
    assert repo.get("p1") == {"a": 1}


def test_save_if_absent_refuses_duplicate():
    repo = make_repo()
    assert repo.save_if_absent("p1", {"first": True}, 60) is True
    assert repo.save_if_absent("p1", {"second": True}, 60) is False
    assert repo.get("p1") == {"first": True}


def test_expired_record_reads_as_none_and_can_be_reclaimed():
    repo = make_repo()
    # ttl -1 => already expired; mongomock has no TTL monitor, so this exercises
    # the lazy liveness check and the atomic reclaim-on-conflict path.
    assert repo.save_if_absent("p1", {"old": True}, -1) is True
    assert repo.get("p1") is None
    assert repo.save_if_absent("p1", {"new": True}, 60) is True
    assert repo.get("p1") == {"new": True}


def test_update_and_delete():
    repo = make_repo()
    repo.save_if_absent("p1", {"v": 1}, 60)
    repo.update("p1", {"v": 2}, 60)
    assert repo.get("p1") == {"v": 2}
    repo.delete("p1")
    assert repo.get("p1") is None


def test_concurrent_first_saves_exactly_one_winner():
    repo = make_repo()
    barrier = threading.Barrier(8)
    results: list[bool] = []
    lock = threading.Lock()

    def attempt(i: int) -> None:
        barrier.wait()
        created = repo.save_if_absent("race", {"writer": i}, 60)
        with lock:
            results.append(created)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1
    assert repo.get("race") is not None
