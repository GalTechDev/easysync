"""
Encoding and decoding of attribute updates, shared by SyncClient and SHMSyncClient.

An update is a metadata dict (pickled) plus an optional raw binary payload.
Delta bases are tracked per sender: a delta from peer A is always applied on
the last value received from A, whatever other peers sent in between.
"""

import os

from easysync.codecs import find_codec, get_codec, decode_value, snapshot


def new_client_id():
    return os.urandom(8).hex()


def _raw_buffer(value):
    """Return (memoryview, type tag) if value is a raw binary buffer, else None."""
    if isinstance(value, bytes):
        return memoryview(value), "bytes"
    if isinstance(value, (bytearray, memoryview)):
        return memoryview(value).cast("B") if isinstance(value, memoryview) else memoryview(value), "bytearray"
    # EasySHM objects: their mapped buffer
    if hasattr(value, "_data") and hasattr(value._data, "buf"):
        return memoryview(value._data.buf), "bytearray"
    if type(value).__name__ == "mmap":
        return memoryview(value), "bytearray"
    return None


def _as_raw(src):
    if isinstance(src, (bytes, bytearray, memoryview)):
        mv = memoryview(src)
        return mv.cast("B") if mv.ndim != 1 or mv.format != "B" else mv
    if hasattr(src, "_data") and hasattr(src._data, "buf"):
        return memoryview(src._data.buf)
    return None


class DeltaState:
    def __init__(self):
        self.sent = {}      # (object_id, attr) -> snapshot of the last value we sent
        self.received = {}  # (sender, object_id, attr) -> last value received from sender

    def forget_sent(self, key=None):
        if key is None:
            self.sent.clear()
        else:
            self.sent.pop(key, None)


def encode_update(state, sender, object_id, attr_name, value, allow_delta=True):
    """Build (packet, raw_payload) for an attribute update."""
    packet = {"type": "update", "object_id": object_id, "attr_name": attr_name, "_src": sender}

    raw = _raw_buffer(value)
    if raw is not None:
        mv, tag = raw
        packet["_raw_size"] = mv.nbytes
        packet["_raw_type"] = tag
        return packet, mv

    result = find_codec(value)
    if not result:
        packet["value"] = value
        return packet, None

    codec_name, c = result
    key = (object_id, attr_name)
    packet["_codec"] = codec_name

    if allow_delta and c.supports_delta():
        old = state.sent.get(key)
        if old is not None:
            delta = c.encode_delta(old, value)
            if delta is not None:
                packet["value"] = delta
                packet["_delta"] = True
                state.sent[key] = snapshot(c, value)
                return packet, None

    encoded = c.encode(value)
    if allow_delta and c.supports_delta():
        state.sent[key] = snapshot(c, value)
    else:
        state.sent.pop(key, None)

    if isinstance(encoded, tuple) and len(encoded) == 2:
        meta, src = encoded
        mv = _as_raw(src)
        if mv is not None:
            packet["value"] = meta
            packet["_raw_size"] = mv.nbytes
            return packet, mv
    packet["value"] = encoded
    return packet, None


class NeedFullValue(Exception):
    """A delta arrived without its base: the sender must resend the full value."""


def decode_update(state, message, raw):
    """Turn a received update into its final value (stored in message["value"]).

    Raises NeedFullValue when a delta cannot be applied.
    """
    sender = message.get("_src")
    oid, attr = message.get("object_id"), message.get("attr_name")

    if "_codec" in message:
        c = get_codec(message["_codec"])
        if c is None:
            raise ValueError(f"no codec registered for {message['_codec']!r}")
        key = (sender, oid, attr)
        if message.get("_delta"):
            base = state.received.get(key)
            if base is None:
                raise NeedFullValue(key)
            value = c.decode_delta(base, message["value"])
        else:
            value = decode_value(c, message.get("value"), raw)
        if c.supports_delta():
            state.received[key] = snapshot(c, value)
        message["value"] = value
    elif raw is not None:
        message["value"] = bytes(raw) if message.get("_raw_type") == "bytes" else raw

    for k in ("_codec", "_delta", "_raw_size", "_raw_type", "_src", "_useq"):
        message.pop(k, None)
    return message
