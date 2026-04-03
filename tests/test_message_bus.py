"""Tests for the internal message bus."""

import threading
from core.message_bus import MessageBus


def test_publish_subscribe():
    bus = MessageBus()
    received = []
    bus.subscribe("test.topic", lambda msg: received.append(msg))
    bus.publish("test.topic", "hello")
    assert received == ["hello"]


def test_multiple_subscribers():
    bus = MessageBus()
    a, b = [], []
    bus.subscribe("t", lambda msg: a.append(msg))
    bus.subscribe("t", lambda msg: b.append(msg))
    bus.publish("t", 42)
    assert a == [42]
    assert b == [42]


def test_unsubscribe():
    bus = MessageBus()
    received = []
    cb = lambda msg: received.append(msg)
    bus.subscribe("t", cb)
    bus.publish("t", 1)
    bus.unsubscribe("t", cb)
    bus.publish("t", 2)
    assert received == [1]


def test_error_isolation():
    """A failing subscriber must not prevent others from receiving."""
    bus = MessageBus()
    received = []

    def bad_cb(msg):
        raise RuntimeError("boom")

    bus.subscribe("t", bad_cb)
    bus.subscribe("t", lambda msg: received.append(msg))
    bus.publish("t", "ok")
    assert received == ["ok"]


def test_no_cross_topic():
    bus = MessageBus()
    received = []
    bus.subscribe("a", lambda msg: received.append(msg))
    bus.publish("b", "nope")
    assert received == []


def test_thread_safety():
    bus = MessageBus()
    results = []
    barrier = threading.Barrier(10)

    def publisher(i):
        barrier.wait()
        bus.publish("t", i)

    bus.subscribe("t", lambda msg: results.append(msg))
    threads = [threading.Thread(target=publisher, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == list(range(10))
