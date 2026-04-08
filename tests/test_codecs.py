"""
Tests for the EasySync Codec Extension System.
"""

import sys
import os
import time
import socket
import threading
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from easysync import SyncServer, SyncedObject, register_codec, codec, list_codecs
from easysync.syncclient import SyncClient
from easysync.codecs import _registry, find_codec, get_codec, get_excluded_types


def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for(condition, timeout=3.0, interval=0.05):
    start = time.time()
    while time.time() - start < timeout:
        if condition():
            return True
        time.sleep(interval)
    return False


# -------- Test codec registration --------

def test_register_codec_functional():
    """Test functional codec registration."""
    # Clean up after test
    old_registry = dict(_registry)

    register_codec(
        name="test.MyType",
        match=lambda obj: type(obj).__name__ == "MyType",
        encode=lambda obj: b"encoded",
        decode=lambda data: "decoded",
        deep_proxy=False,
    )

    assert "test.MyType" in _registry
    assert get_codec("test.MyType") is not None
    assert "test.MyType" in list_codecs()

    # Cleanup
    _registry.clear()
    _registry.update(old_registry)


def test_register_codec_decorator():
    """Test class-based codec registration via decorator."""
    old_registry = dict(_registry)

    @codec("test.Decorated")
    class DecoratedCodec:
        deep_proxy = False

        def match(self, obj):
            return isinstance(obj, float)

        def encode(self, obj):
            return str(obj).encode()

        def decode(self, data):
            return float(data.decode())

    assert "test.Decorated" in _registry
    c = get_codec("test.Decorated")
    assert c is not None
    assert c.match(3.14)
    assert c.encode(3.14) == b"3.14"
    assert c.decode(b"3.14") == 3.14

    # Cleanup
    _registry.clear()
    _registry.update(old_registry)


def test_find_codec():
    """Test that find_codec correctly matches objects."""
    old_registry = dict(_registry)

    register_codec(
        name="test.bytes_codec",
        match=lambda obj: isinstance(obj, bytearray),
        encode=lambda obj: bytes(obj),
        decode=lambda data: bytearray(data),
        deep_proxy=False,
    )

    result = find_codec(bytearray(b"hello"))
    assert result is not None
    name, c = result
    assert name == "test.bytes_codec"

    # Non-matching type returns None
    assert find_codec("a string") is None

    # Cleanup
    _registry.clear()
    _registry.update(old_registry)


def test_excluded_types():
    """Test that get_excluded_types includes defaults and codec types."""
    old_registry = dict(_registry)

    excluded = get_excluded_types()
    # Defaults should always be there
    assert "ndarray" in excluded
    assert "DataFrame" in excluded
    assert "Series" in excluded

    # Register a codec with deep_proxy=False
    register_codec(
        name="torch.Tensor",
        match=lambda obj: False,
        encode=lambda obj: b"",
        decode=lambda data: None,
        deep_proxy=False,
    )

    excluded = get_excluded_types()
    assert "Tensor" in excluded

    # Cleanup
    _registry.clear()
    _registry.update(old_registry)


# -------- End-to-end test with a mock codec --------

@pytest.fixture
def sync_env_with_codec():
    """Fixture providing a server + client pair with a custom codec."""
    old_registry = dict(_registry)

    # Register a simple codec that reverses bytes
    register_codec(
        name="test.ReversibleBytes",
        match=lambda obj: isinstance(obj, bytearray),
        encode=lambda obj: bytes(obj[::-1]),  # Reverse on encode
        decode=lambda data: bytearray(data[::-1]),  # Reverse back on decode
        deep_proxy=False,
    )

    port = get_free_port()
    server = SyncServer(port=port)
    server.start_thread()
    time.sleep(0.5)

    clientA = SyncClient(host="127.0.0.1", port=port)
    clientA.connect()
    clientB = SyncClient(host="127.0.0.1", port=port)
    clientB.connect()

    wait_for(lambda: clientA.connected and clientB.connected, timeout=3)

    yield clientA, clientB

    # Cleanup
    clientA.connected = False
    clientB.connected = False
    try:
        clientA.client_socket.close()
    except:
        pass
    try:
        clientB.client_socket.close()
    except:
        pass

    _registry.clear()
    _registry.update(old_registry)


def test_codec_end_to_end(sync_env_with_codec):
    """Test that a registered codec is actually used during sync."""
    clientA, clientB = sync_env_with_codec

    received = {}
    event = threading.Event()

    def on_update(msg):
        received["value"] = msg.get("value")
        event.set()

    clientB.register_callback("codec_test", on_update)

    # Send a bytearray — the codec should encode/decode it transparently
    original = bytearray(b"Hello EasySync")
    clientA.send_update("codec_test", "data", original)

    assert event.wait(timeout=3), "Did not receive update"
    assert received["value"] == original, f"Expected {original}, got {received['value']}"


def test_codec_with_synced_object(sync_env_with_codec):
    """Test codec integration with @SyncedObject."""
    clientA, clientB = sync_env_with_codec

    @SyncedObject()
    class Container:
        def __init__(self):
            self.payload = bytearray(b"")

    objA = Container(_sync_client=clientA)
    objB = Container(_sync_client=clientB)

    objA.payload = bytearray(b"codec works!")

    assert wait_for(lambda: objB.payload == bytearray(b"codec works!"), timeout=3)
