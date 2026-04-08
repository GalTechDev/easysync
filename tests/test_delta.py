"""
Test Delta Sync functionality.
"""

import sys
import os
import time
import socket
import threading
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np

from easysync import SyncServer, SyncedObject
from easysync.syncclient import SyncClient
from easysync.codecs import _registry

# Register numpy codec with delta support
import easysync.contrib.numpy_codec  # noqa


def get_free_port():
    while True:
        s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s1.bind(("", 0))
        port = s1.getsockname()[1]
        s1.close()
        try:
            s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s2.bind(("", port + 1))
            s2.close()
            return port
        except OSError:
            continue


def wait_for(condition, timeout=3.0, interval=0.05):
    start = time.time()
    while time.time() - start < timeout:
        if condition():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def delta_env():
    """Fixture with two connected clients and numpy codec registered."""
    port = get_free_port()
    server = SyncServer(port=port)
    server.start_thread()
    time.sleep(0.5)

    cA = SyncClient(host="127.0.0.1", port=port)
    cA.connect()
    cB = SyncClient(host="127.0.0.1", port=port)
    cB.connect()

    wait_for(lambda: cA.connected and cB.connected, timeout=3)

    yield cA, cB

    cA.connected = False
    cB.connected = False
    try: cA.client_socket.close()
    except: pass
    try: cB.client_socket.close()
    except: pass


def test_delta_codec_supports_delta():
    """Test that the numpy codec reports delta support."""
    from easysync.codecs import get_codec
    c = get_codec("numpy.ndarray")
    assert c is not None
    assert c.supports_delta()


def test_delta_encode_decode_roundtrip():
    """Test that encode_delta + decode_delta produces the correct result."""
    from easysync.codecs import get_codec
    c = get_codec("numpy.ndarray")

    old = np.array([1.0, 2.0, 3.0, 4.0])
    new = np.array([1.0, 2.0, 3.5, 4.0])  # One value changed

    delta = c.encode_delta(old, new)
    if delta is not None:
        result = c.decode_delta(old, delta)
        np.testing.assert_array_equal(result, new)


def test_delta_returns_none_for_different_shapes():
    """Delta should return None if shapes differ."""
    from easysync.codecs import get_codec
    c = get_codec("numpy.ndarray")

    old = np.array([1.0, 2.0, 3.0])
    new = np.array([1.0, 2.0, 3.0, 4.0])

    assert c.encode_delta(old, new) is None


def test_delta_sync_end_to_end(delta_env):
    """Test delta sync works through the full network pipeline."""
    cA, cB = delta_env

    received_values = []
    event = threading.Event()

    def on_update(msg):
        received_values.append(msg["value"].copy())
        event.set()

    cB.register_callback("delta_test", on_update)

    # First send: full (no previous cache)
    arr1 = np.zeros(1000, dtype=np.float64)
    cA.send_update("delta_test", "data", arr1)
    assert event.wait(timeout=3)
    np.testing.assert_array_equal(received_values[-1], arr1)

    # Second send: delta (only 1% changed)
    event.clear()
    arr2 = arr1.copy()
    arr2[500] = 42.0
    cA.send_update("delta_test", "data", arr2)
    assert event.wait(timeout=3)
    np.testing.assert_array_equal(received_values[-1], arr2)


def test_delta_caching(delta_env):
    """Test that delta cache works across multiple updates."""
    cA, cB = delta_env

    latest = {"val": None}
    event = threading.Event()

    def on_update(msg):
        latest["val"] = msg["value"].copy()
        event.set()

    cB.register_callback("cache_test", on_update)

    base = np.ones(500, dtype=np.float32)

    # Send 5 incremental updates
    for i in range(5):
        event.clear()
        arr = base.copy()
        arr[i * 100] = float(i + 10)
        cA.send_update("cache_test", "data", arr)
        assert event.wait(timeout=3), f"Update {i} not received"
        np.testing.assert_array_equal(latest["val"], arr)
        base = arr  # For next delta
