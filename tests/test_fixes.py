"""
Regression tests for the bugs and security issues fixed in 0.2.0.
"""

import os
import pickle
import socket
import struct
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np

import easysync
from easysync import SyncServer, SyncedObject, serialization
from easysync.codecs import Codec, _registry
from easysync.syncclient import SyncClient


# ---------------------------------------------------------------- helpers

def get_free_port():
    while True:
        s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s1.bind(("127.0.0.1", 0))
        port = s1.getsockname()[1]
        s1.close()
        try:
            s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s2.bind(("127.0.0.1", port + 1))
            s2.close()
            return port
        except OSError:
            continue


def wait_for(condition, timeout=3.0, interval=0.02):
    start = time.time()
    while time.time() - start < timeout:
        if condition():
            return True
        time.sleep(interval)
    return False


def start_server(**kwargs):
    port = get_free_port()
    server = SyncServer(host="127.0.0.1", port=port, **kwargs)
    server.start_thread()  # returns once the server accepts connections
    return server, port


def new_client(port, **kwargs):
    kwargs.setdefault("sync_new_client", False)
    c = SyncClient(host="127.0.0.1", port=port, **kwargs)
    assert c.connect(timeout=5)
    return c


@pytest.fixture
def cluster():
    """A server and a factory of connected clients, all closed after the test."""
    server, port = start_server()
    clients = []

    def make(**kwargs):
        c = new_client(port, **kwargs)
        clients.append(c)
        return c

    make.port = port
    make.server = server
    yield make
    for c in clients:
        c.close()


def collect(client, object_id):
    got = []
    client.register_callback(object_id, lambda msg: got.append(msg["value"]))
    return got


class _Evil:
    """Pickles into a call that creates a marker file if it is ever executed."""

    def __init__(self, path):
        self.path = path

    def __reduce__(self):
        return (open, (self.path, "w"))


def evil_frame(message):
    raw = pickle.dumps(message)
    return struct.pack(">I", len(raw)) + raw


@pytest.fixture
def marker():
    path = os.path.join(tempfile.gettempdir(), f"easysync_pwned_{uuid.uuid4().hex}")
    yield path
    if os.path.exists(path):
        os.remove(path)


# ---------------------------------------------------------------- NumPy (raw payload bug)

def test_numpy_arrays_keep_dtype_and_shape(cluster):
    a, b = cluster(), cluster()
    got = collect(b, "Arr")
    arrays = [np.arange(6, dtype=np.float32).reshape(2, 3),
              np.arange(24, dtype=np.int64).reshape(2, 3, 4),
              np.asfortranarray(np.arange(12, dtype=np.float64).reshape(3, 4)),  # non-contiguous in C order
              np.zeros(0, dtype=np.uint16)]
    for arr in arrays:
        a.send_update("Arr", "v", arr)
    assert wait_for(lambda: len(got) == len(arrays))
    for sent, received in zip(arrays, got):
        assert isinstance(received, np.ndarray)
        assert received.dtype == sent.dtype and received.shape == sent.shape
        np.testing.assert_array_equal(received, sent)


def test_connection_survives_numpy_arrays(cluster):
    a, b = cluster(), cluster()
    arrays, ints = collect(b, "Arr"), collect(b, "Int")
    a.send_update("Arr", "v", np.ones((4, 4), dtype=np.float32))
    for i in range(10):
        a.send_update("Int", "v", i)
    assert wait_for(lambda: len(ints) == 10)
    assert ints == list(range(10)) and len(arrays) == 1


def test_numpy_through_synced_object(cluster):
    a, b = cluster(), cluster()

    @SyncedObject()
    class Frame:
        def __init__(self):
            self.pixels = np.zeros((4, 4, 3), dtype=np.uint8)

    fa, fb = Frame(_sync_client=a), Frame(_sync_client=b)
    fa.pixels = np.full((4, 4, 3), 7, dtype=np.uint8)
    assert wait_for(lambda: isinstance(fb.pixels, np.ndarray) and fb.pixels.sum() == 7 * 48)


def test_bytes_stay_bytes(cluster):
    a, b = cluster(), cluster()
    got = collect(b, "Blob")
    a.send_update("Blob", "v", b"hello")
    a.send_update("Blob", "v", bytearray(b"world"))
    assert wait_for(lambda: len(got) == 2)
    assert type(got[0]) is bytes and got[0] == b"hello"
    assert type(got[1]) is bytearray and got[1] == b"world"


# ---------------------------------------------------------------- codecs

class Point:
    def __init__(self, x):
        self.x = x


class PointCodec(Codec):
    def match(self, obj):
        return isinstance(obj, Point)

    def encode(self, obj):
        return str(obj.x).encode()

    def decode(self, data):  # documented signature, without raw_payload
        return Point(int(data))


def test_custom_codec_with_documented_decode_signature(cluster):
    old = dict(_registry)
    easysync.register_codec("test.Point", PointCodec())
    try:
        a, b = cluster(), cluster()
        got = collect(b, "P")
        a.send_update("P", "v", Point(7))
        a.send_update("P", "v", 123)
        assert wait_for(lambda: len(got) == 2)
        assert isinstance(got[0], Point) and got[0].x == 7 and got[1] == 123
        assert b.connected
    finally:
        _registry.clear()
        _registry.update(old)


def test_register_codec_accepts_match_keyword():
    old = dict(_registry)
    try:
        c = easysync.register_codec("test.kw", match=lambda o: o == "x", encode=lambda o: b"x",
                                    decode=lambda d: "x")
        assert c.match("x")
    finally:
        _registry.clear()
        _registry.update(old)


# ---------------------------------------------------------------- delta sync

def test_delta_base_is_tracked_per_sender(cluster):
    a, b, c = cluster(), cluster(), cluster()
    got = collect(c, "M")
    v1 = np.zeros(10_000, dtype=np.float32)
    v2 = np.full(10_000, 5, dtype=np.float32)
    v3 = v1.copy()
    v3[:10] = 1  # small change from A's last value: sent as a delta against v1
    a.send_update("M", "v", v1)
    assert wait_for(lambda: len(got) == 1)
    b.send_update("M", "v", v2)
    assert wait_for(lambda: len(got) == 2)
    a.send_update("M", "v", v3)
    assert wait_for(lambda: len(got) == 3)
    np.testing.assert_array_equal(got[-1], v3)


def test_delta_sees_in_place_changes(cluster):
    a, b = cluster(), cluster()
    got = collect(b, "M")
    arr = np.zeros(10_000, dtype=np.float32)
    a.send_update("M", "v", arr)
    arr[:5] = 9  # same object, modified in place
    a.send_update("M", "v", arr)
    assert wait_for(lambda: len(got) == 2)
    np.testing.assert_array_equal(got[-1], arr)


def test_late_joiner_gets_full_value_instead_of_orphan_delta(cluster):
    a, b = cluster(), cluster()
    arr = np.zeros(10_000, dtype=np.float32)
    a.send_update("M", "v", arr)
    time.sleep(0.2)
    late = cluster()  # never saw the full value
    got = collect(late, "M")
    arr[:5] = 3
    a.send_update("M", "v", arr)  # delta: late has no base -> asks A for the full value
    assert wait_for(lambda: len(got) >= 1)
    np.testing.assert_array_equal(got[-1], arr)


def test_udp_objects_never_send_deltas_as_datagrams(cluster):
    a, b = cluster(), cluster()
    got = collect(b, "Frame")
    datagrams = []
    real_sendto = a._udp_socket.sendto

    class Spy:
        def __getattr__(self, name):
            return getattr(a._udp_socket_real, name)

        def sendto(self, data, addr):
            datagrams.append(pickle.loads(data))
            return real_sendto(data, addr)
    a._udp_socket_real = a._udp_socket
    a._udp_socket = Spy()

    small = np.zeros(100, dtype=np.uint8)
    a.send_update("Frame", "small", small, transport="udp")      # fits in a datagram
    small[0] = 1
    a.send_update("Frame", "small", small, transport="udp")      # full again: datagram bases are not kept
    frame = np.zeros((360, 640, 3), dtype=np.uint8)              # 690 KB: goes over TCP
    for i in range(4):
        frame[10 * i:10 * i + 10, :50] = 200 + i
        a.send_update("Frame", "big", frame, transport="udp")     # full, then deltas over TCP
    assert wait_for(lambda: len(got) == 6, timeout=5)
    assert len(datagrams) == 2 and not any(d.get("_delta") for d in datagrams)
    np.testing.assert_array_equal(got[1], small)
    np.testing.assert_array_equal(got[-1], frame)


def test_late_udp_datagrams_are_dropped(cluster):
    b = cluster()
    got = collect(b, "Pos")
    base = {"type": "update", "object_id": "Pos", "attr_name": "x", "_src": "peer"}
    b._dispatch_message(dict(base, value=2, _useq=2))
    b._dispatch_message(dict(base, value=1, _useq=1))  # arrives after a newer one
    b._dispatch_message(dict(base, value=3, _useq=3))
    assert got == [2, 3]


# ---------------------------------------------------------------- SyncedObject

def test_two_instances_of_one_class_stay_separate(cluster):
    a, b = cluster(), cluster()

    @SyncedObject()
    class Player:
        def __init__(self):
            self.x = 0

    a1, a2 = Player(_sync_client=a), Player(_sync_client=a)
    b1, b2 = Player(_sync_client=b), Player(_sync_client=b)
    a1.x, a2.x = 1, 2
    assert wait_for(lambda: (b1.x, b2.x) == (1, 2))


def test_explicit_sync_id(cluster):
    a, b = cluster(), cluster()

    @SyncedObject()
    class Player:
        def __init__(self):
            self.x = 0

    alice_a = Player(_sync_client=a, _sync_id="alice")
    Player(_sync_client=b)  # created first on b, but not the same id
    alice_b = Player(_sync_client=b, _sync_id="alice")
    alice_a.x = 5
    assert wait_for(lambda: alice_b.x == 5)


def test_update_right_after_connect_is_not_lost():
    server, port = start_server()
    receiver = new_client(port)
    got = collect(receiver, "State")
    sender = easysync.connect("127.0.0.1", port, sync_new_client=False)  # waits for the connection
    sender.send_update("State", "score", 42)  # no sleep in between
    try:
        assert wait_for(lambda: got == [42])
    finally:
        sender.close()
        receiver.close()


# ---------------------------------------------------------------- client internals

def test_telemetry_counts_and_latency(cluster):
    a, b = cluster(), cluster()
    for i in range(20):
        a.send_update("X", "v", i)
    assert wait_for(lambda: b.stats["packets_recv"] >= 20)
    b.ping()
    assert wait_for(lambda: b.stats["latency_ms"] > 0)


def test_concurrent_sends_from_threads(cluster):
    a, b = cluster(), cluster()
    small, blobs = collect(b, "S"), collect(b, "B")
    blob = os.urandom(256_000)
    t1 = threading.Thread(target=lambda: [a.send_update("B", "d", blob) for _ in range(50)])
    t2 = threading.Thread(target=lambda: [a.send_update("S", "v", i) for i in range(2000)])
    t1.start(); t2.start(); t1.join(); t2.join()
    assert wait_for(lambda: len(small) == 2000 and len(blobs) == 50, timeout=10)
    assert small == list(range(2000)) and all(x == blob for x in blobs)


def test_callback_error_does_not_disconnect(cluster):
    a, b = cluster(), cluster()
    got = []

    def cb(msg):
        if msg["value"] == "boom":
            raise RuntimeError("user bug")
        got.append(msg["value"])
    b.register_callback("X", cb)
    a.send_update("X", "v", "boom")
    a.send_update("X", "v", "ok")
    assert wait_for(lambda: got == ["ok"])
    assert b.connected


# ---------------------------------------------------------------- server

def test_slow_client_does_not_block_the_others():
    server, port = start_server(send_queue_size=10, slow_client_timeout=0.5)
    # A client that authenticates and then never reads
    stuck = socket.create_connection(("127.0.0.1", port))
    stuck.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    stuck.sendall(evil_frame({"type": "auth", "payload": None}))
    a, b = new_client(port), new_client(port)
    got = collect(b, "X")
    blob = os.urandom(64_000)
    try:
        for i in range(200):
            a.send_update("X", "v", blob)
        assert wait_for(lambda: len(got) == 200, timeout=10)
    finally:
        stuck.close(); a.close(); b.close()


def test_oversized_message_closes_the_connection():
    server, port = start_server(max_message_size=1000)
    a, b = new_client(port), new_client(port)
    got = collect(b, "X")
    try:
        a.send_update("X", "v", os.urandom(5000))
        assert wait_for(lambda: not a.connected, timeout=3)  # the server closed the connection
        assert got == []
    finally:
        a.close(); b.close()


def test_start_thread_is_ready_immediately():
    server, port = start_server()
    socket.create_connection(("127.0.0.1", port), timeout=1).close()


# ---------------------------------------------------------------- security

def test_server_never_unpickles_before_auth(marker):
    server, port = start_server()

    @server.on_auth
    def deny(addr, payload):
        return False

    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(evil_frame({"type": "auth", "payload": _Evil(marker)}))
    s.sendall(evil_frame({"type": "update", "value": _Evil(marker)}))
    time.sleep(0.5)
    s.close()
    assert not os.path.exists(marker)


def test_relayed_malicious_update_is_refused_by_clients(cluster, marker):
    b = cluster()
    got = collect(b, "X")
    attacker = socket.create_connection(("127.0.0.1", cluster.port))
    attacker.sendall(evil_frame({"type": "auth", "payload": None, "udp_port": 1}))
    attacker.recv(1024)  # auth_ok
    attacker.sendall(evil_frame({"type": "update", "object_id": "X", "attr_name": "v", "value": _Evil(marker)}))
    time.sleep(0.3)
    a = cluster()
    a.send_update("X", "v", "still alive")
    assert wait_for(lambda: got == ["still alive"])
    assert not os.path.exists(marker)
    attacker.close()


def test_spoofed_udp_is_ignored(cluster, marker):
    b = cluster()
    got = collect(b, "X")
    payload = pickle.dumps({"type": "update", "object_id": "X", "attr_name": "v", "value": _Evil(marker)})
    spoof = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    spoof.sendto(payload, ("127.0.0.1", cluster.port + 1))      # via the relay, unauthenticated
    spoof.sendto(payload, b._udp_socket.getsockname())           # straight to the client
    spoof.close()
    time.sleep(0.5)
    assert got == [] and not os.path.exists(marker)


@dataclass
class Settings:
    volume: int
    name: str


def test_allow_types_and_trust_all(cluster):
    a, b = cluster(), cluster()
    got = collect(b, "S")
    a.send_update("S", "v", Settings(3, "x"))          # refused: not allowed yet
    time.sleep(0.3)
    assert got == [] and b.connected
    easysync.allow_types(Settings)
    try:
        a.send_update("S", "v", Settings(4, "y"))
        assert wait_for(lambda: got == [Settings(4, "y")])
    finally:
        serialization._user_globals.discard((Settings.__module__, Settings.__qualname__))


def test_restricted_loads_accepts_plain_data():
    import datetime
    import decimal
    value = {"a": [1, 2.5, "s", b"b", None, True], "t": (1, 2), "set": {1}, "fs": frozenset({2}),
             "ba": bytearray(b"x"), "c": 1 + 2j, "dt": datetime.datetime(2026, 1, 1), "d": decimal.Decimal("1.5"),
             "np": np.arange(3), "scalar": np.float64(2.5)}
    out = serialization.loads(serialization.dumps(value))
    np.testing.assert_array_equal(out.pop("np"), value.pop("np"))
    assert out == value
    with pytest.raises(serialization.UnsafeTypeError):
        serialization.loads(pickle.dumps(_Evil("/nonexistent")))


# ---------------------------------------------------------------- SHM mode

import importlib.util

requires_shm = pytest.mark.skipif(importlib.util.find_spec("easyshm") is None, reason="py-easyshm not installed")


def _shm_pair(buffer_size=16 * 1024 * 1024):
    from easysync.shm_client import SHMSyncClient
    name = f"t{uuid.uuid4().hex[:8]}"
    a = SHMSyncClient(name, buffer_size=buffer_size, sync_new_client=False)
    b = SHMSyncClient(name, buffer_size=buffer_size, sync_new_client=False)
    a.connect(); b.connect()
    return a, b


@requires_shm
def test_shm_burst_is_not_lost():
    a, b = _shm_pair()
    got = collect(b, "Burst")
    try:
        for i in range(500):
            a.send_update("Burst", f"a{i}", i)
        assert wait_for(lambda: len(got) == 500, timeout=10)
        assert got == list(range(500))
    finally:
        a.close(); b.close()


@requires_shm
def test_shm_ring_wraps_around():
    a, b = _shm_pair(buffer_size=64 * 1024)
    got = collect(b, "Arr")
    try:
        for i in range(300):  # ~3 MB through a 64 KB ring
            a.send_update("Arr", "v", np.full(2500, i, dtype=np.float32))
            if i % 10 == 0:
                wait_for(lambda: len(got) > i - 5, timeout=2)
        assert wait_for(lambda: len(got) == 300, timeout=10)
        assert all(arr[0] == i and arr.shape == (2500,) for i, arr in enumerate(got))
    finally:
        a.close(); b.close()


@requires_shm
def test_shm_rejects_updates_larger_than_the_ring():
    a, b = _shm_pair(buffer_size=64 * 1024)
    got = collect(b, "Big")
    try:
        a.send_update("Big", "v", os.urandom(100_000))  # printed error, not sent
        a.send_update("Big", "v", b"small")
        assert wait_for(lambda: got == [b"small"])
    finally:
        a.close(); b.close()
