import multiprocessing as mp
import time
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from shimpaku import lock


def _hold(db, device, holder, hold_s, started, out):
    lease = lock.acquire(device, holder, "test", db=db, wait=30, poll=0.05)
    out.put((holder, "got", time.time()))
    started.set()
    time.sleep(hold_s)
    out.put((holder, "release", time.time()))
    lock.release(lease, db=db)


def test_second_process_waits_for_first(tmp_path):
    db = tmp_path / "locks.db"
    ctx = mp.get_context("spawn")
    out, started = ctx.Queue(), ctx.Event()
    a = ctx.Process(target=_hold, args=(db, "leaf1", "executor", 1.0, started, out))
    a.start()
    started.wait(10)
    b_started = ctx.Event()  # keep a reference: an unreferenced Event is freed before the child opens it
    b = ctx.Process(target=_hold, args=(db, "leaf1", "bench", 0.1, b_started, out))
    b.start()
    a.join(20), b.join(20)
    events = [out.get(timeout=5) for _ in range(4)]
    a_release = next(t for h, e, t in events if h == "executor" and e == "release")
    b_got = next(t for h, e, t in events if h == "bench" and e == "got")
    assert b_got >= a_release, "bench got the device while the executor still held it"


def test_other_devices_do_not_block(tmp_path):
    db = tmp_path / "locks.db"
    lock.acquire("leaf1", "executor", "t", db=db)
    t0 = time.monotonic()
    lock.acquire("leaf2", "bench", "t", db=db, wait=0)
    assert time.monotonic() - t0 < 1


def test_busy_raises_after_wait(tmp_path):
    db = tmp_path / "locks.db"
    lock.acquire("spine1", "executor", "t", db=db)
    with pytest.raises(lock.DeviceBusy):
        lock.acquire("spine1", "bench", "t", db=db, wait=0.3, poll=0.05)


def test_expired_lease_is_taken_over(tmp_path):
    db = tmp_path / "locks.db"
    lock.acquire("r1", "crashed-writer", "t", db=db, ttl=0.2)
    time.sleep(0.3)
    lease = lock.acquire("r1", "bench", "t", db=db, wait=0)
    assert lease.holder == "bench"


def test_release_does_not_drop_someone_elses_lease(tmp_path):
    db = tmp_path / "locks.db"
    old = lock.acquire("r2", "slow", "t", db=db, ttl=0.2)
    time.sleep(0.3)
    lock.acquire("r2", "new", "t", db=db, wait=0)
    lock.release(old, db=db)
    assert [h["holder"] for h in lock.holders(db=db)] == ["new"]


def _fake_dev(xml: str):
    return SimpleNamespace(rpc=SimpleNamespace(get_commit_information=lambda: ET.fromstring(xml)))


# captured from vJunos-router 26.2R1.7 (r2), 2026-10-23
REAL_PENDING = ("<commit-information><commit-history><sequence-number>0</sequence-number><user>admin</user>"
                "<client>cli</client><date-time seconds=\"1792788958\">2026-10-23 20:55:58 UTC</date-time>"
                "<comment>commit confirmed, rollback in 3mins</comment>"
                "<commit-confirmed>rollback pending</commit-confirmed></commit-history>"
                "<commit-history><sequence-number>1</sequence-number></commit-history></commit-information>")
REAL_SETTLED = ("<commit-information><commit-history><sequence-number>0</sequence-number><user>admin</user>"
                "<client>cli</client><date-time seconds=\"1792788968\">2026-10-23 20:56:08 UTC</date-time>"
                "</commit-history><commit-history><sequence-number>2</sequence-number>"
                "<comment>commit confirmed, rollback in 3mins</comment></commit-history></commit-information>")


def test_pending_confirm_on_real_vjunos_xml():
    assert lock.pending_confirm(_fake_dev(REAL_PENDING)) is True
    assert lock.pending_confirm(_fake_dev(REAL_SETTLED)) is False, "an old confirmed entry must not count"


def test_pending_confirm_detected():
    pending = ("<commit-information><commit-history><sequence-number>0</sequence-number>"
               "<user>admin</user><client>cli</client><date-time>x</date-time>"
               "<comment>commit confirmed, rollback in 5mins</comment></commit-history></commit-information>")
    settled = ("<commit-information><commit-history><sequence-number>0</sequence-number>"
               "<user>admin</user><client>cli</client></commit-history></commit-information>")
    assert lock.pending_confirm(_fake_dev(pending)) is True
    assert lock.pending_confirm(_fake_dev(settled)) is False


def test_device_lock_refuses_when_confirm_pending(tmp_path):
    db = tmp_path / "locks.db"
    pending = ("<commit-information><commit-history><comment>commit confirmed, rollback in 5mins"
               "</comment></commit-history></commit-information>")
    with pytest.raises(lock.PendingConfirm):
        with lock.device_lock("leaf1", "executor", "t", dev=_fake_dev(pending), db=db):
            pass
    assert lock.holders(db=db) == [], "a refused write must not keep the lease"
