"""
EasySync — SHM Sync Client
============================
A drop-in replacement for SyncClient that uses EasySHM instead of TCP/UDP sockets.
Zero-network, zero-server local IPC.

Usage:
    from easysync import shm_connect, SyncedObject

    client = shm_connect("my_app")

    @SyncedObject(client)
    class State:
        def __init__(self):
            self.score = 0

    state = State()
    state.score = 42  # Propagated via shared memory
"""

import struct
import threading

from easysync import serialization
from easysync._wire import DeltaState, NeedFullValue, decode_update, encode_update, new_client_id


# --- Message ring layout (EasySHM data region) ---
#   [0:8]    tail: total bytes ever appended (uint64). Written last, publishes a record.
#   [8:16]   ring capacity in bytes (uint64), set by the first process.
#   [64:..]  ring of records, wrapping around:
#            [u32 record length][u32 metadata length][pickled metadata][raw payload]
#
# Writers append under an inter-process mutex. Readers keep their own position
# and read every record between it and the tail, so bursts are never
# overwritten before being read (unless a reader falls a whole ring behind,
# in which case it asks every peer to resend its state).

_U64 = struct.Struct("<Q")
_REC = struct.Struct("<II")
_DATA_START = 64


class SHMSyncClient:
    """A SyncClient-compatible class that uses shared memory for IPC.

    All processes using the same cluster_name share state through RAM.
    No SyncServer is needed.
    """

    def __init__(self, cluster_name: str, buffer_size: int = 16 * 1024 * 1024, sync_new_client: bool = True):
        try:
            from easyshm import EasySHM
            from easyshm.platform import Mutex
        except ImportError as e:
            raise ImportError("SHM mode needs EasySHM: pip install \"py-easysync[shm]\"") from e

        self.cluster_name = cluster_name
        self.sync_new_client = sync_new_client
        self.connected = False
        self.callbacks = {}
        self._unclaimed_updates = {}
        self.on_sync_request_callback = None
        self.client_id = new_client_id()
        self._delta = DeltaState()

        # Telemetry (compatible with SyncClient.stats)
        self.stats = {
            "bytes_sent": 0,
            "bytes_recv": 0,
            "packets_sent": 0,
            "packets_recv": 0,
            "latency_ms": 0,
        }

        name = f"easysync_{cluster_name}_ring"
        self._bus = EasySHM(name, size=_DATA_START + buffer_size, auto_grow=False)
        self._lock = Mutex(name + "_w")
        self._view = self._bus.read_view(0, self._bus.capacity)

        with self._locked():
            cap = _U64.unpack_from(self._view, 8)[0]
            if cap == 0:
                cap = self._bus.capacity - _DATA_START
                self._bus.write(_U64.pack(cap), offset=8)
        self._cap = cap
        self._pos = self._tail()

        self._active = True
        self._listener = threading.Thread(target=self._listener_loop, daemon=True)

    # ------------------------------------------------------------------ public API

    def connect(self, timeout=None):
        """Activate the SHM client. No network connection needed."""
        self.connected = True
        self._listener.start()
        print(f"[EasySync-SHM] Connected to cluster '{self.cluster_name}' via shared memory")
        if self.sync_new_client:
            self._append({"type": "request_sync", "_src": self.client_id})
        return True

    def send_update(self, object_id, attr_name, value, transport="shm"):
        """Send an attribute update via the shared memory ring (transport is ignored)."""
        if not self.connected:
            return
        try:
            packet, raw = encode_update(self._delta, self.client_id, object_id, attr_name, value)
            self._append(packet, raw)
        except Exception as e:
            print(f"[EasySync-SHM] Send error: {e}")

    def register_callback(self, object_id, callback):
        """Register a callback for incoming updates on a synced object."""
        self.callbacks[object_id] = callback
        if object_id in self._unclaimed_updates:
            for msg in self._unclaimed_updates[object_id].values():
                callback(msg)
            del self._unclaimed_updates[object_id]

    def ping(self):
        """No-op for SHM (no network round trip to measure)."""
        self.stats["latency_ms"] = 0

    def close(self):
        """Stop the listener and release resources."""
        self._active = False
        self.connected = False
        if self._listener.is_alive():
            self._listener.join(timeout=1)
        if self._bus:
            self._view.release()
            self._bus.close()
            self._bus = None
            self._lock.close()

    # ------------------------------------------------------------------ ring I/O

    def _locked(self):
        client = self

        class _Guard:
            def __enter__(self):
                if client._lock.acquire(timeout_ms=5000) is False:
                    raise TimeoutError("[EasySync-SHM] could not lock the message ring")

            def __exit__(self, *exc):
                client._lock.release()
        return _Guard()

    def _tail(self):
        # Locked read: acts as a memory barrier before reading records
        return _U64.unpack(self._bus.read(size=8, offset=0))[0]

    def _ring_write(self, pos, data):
        data = memoryview(data).cast("B")
        start = pos % self._cap
        first = min(len(data), self._cap - start)
        base = _DATA_START + start
        self._view[base:base + first] = data[:first]
        if first < len(data):
            rest = len(data) - first
            self._view[_DATA_START:_DATA_START + rest] = data[first:]

    def _ring_read(self, pos, size):
        start = pos % self._cap
        first = min(size, self._cap - start)
        base = _DATA_START + start
        out = bytes(self._view[base:base + first])
        if first < size:
            out += bytes(self._view[_DATA_START:_DATA_START + size - first])
        return out

    def _append(self, packet, raw=None):
        meta = serialization.dumps(packet)
        raw_len = len(raw) if raw is not None else 0
        size = _REC.size + len(meta) + raw_len
        if size > self._cap // 2:
            raise ValueError(f"update of {size} bytes is too large for the SHM ring "
                             f"({self._cap} bytes): pass a bigger buffer_size to shm_connect()")
        with self._locked():
            tail = _U64.unpack_from(self._view, 0)[0]
            self._ring_write(tail, _REC.pack(size, len(meta)))
            self._ring_write(tail + _REC.size, meta)
            if raw_len:
                self._ring_write(tail + _REC.size + len(meta), raw)
            # Publishing the new tail bumps write_seq and wakes the readers
            self._bus.write(_U64.pack(tail + size), offset=0)
        self.stats["bytes_sent"] += size
        self.stats["packets_sent"] += 1

    def _read_new(self):
        """Return the records appended since our last read, or None if we fell behind."""
        tail = self._tail()
        if tail == self._pos:
            return []
        if tail - self._pos > self._cap:
            return None
        data = self._ring_read(self._pos, tail - self._pos)
        if self._tail() - self._pos > self._cap:
            return None  # overwritten while we were copying it
        records, offset = [], 0
        while offset < len(data):
            size, meta_len = _REC.unpack_from(data, offset)
            meta = data[offset + _REC.size:offset + _REC.size + meta_len]
            raw = data[offset + _REC.size + meta_len:offset + size]
            records.append((meta, raw))
            offset += size
        self._pos = tail
        return records

    # ------------------------------------------------------------------ dispatch

    def _dispatch_message(self, message, raw=None):
        """Process an incoming message (same logic as SyncClient)."""
        if message.get("_src") == self.client_id:
            return  # our own message

        msg_type = message.get("type")
        if msg_type == "update":
            try:
                decode_update(self._delta, message, raw)
            except NeedFullValue:
                self._append({"type": "delta_nack", "_src": self.client_id, "_target": message.get("_src"),
                              "object_id": message.get("object_id"), "attr_name": message.get("attr_name")})
                return
            oid = message.get("object_id")
            if oid:
                if oid in self.callbacks:
                    self.callbacks[oid](message)
                else:
                    attr_name = message.get("attr_name")
                    if attr_name:
                        self._unclaimed_updates.setdefault(oid, {})[attr_name] = message

        elif msg_type == "request_sync":
            if self.on_sync_request_callback:
                self.on_sync_request_callback()

        elif msg_type == "delta_nack":
            if message.get("_target") == self.client_id:
                key = (message.get("object_id"), message.get("attr_name"))
                value = self._delta.sent.get(key)
                if value is not None:
                    self._delta.forget_sent(key)
                    self.send_update(key[0], key[1], value)

    def _listener_loop(self):
        """Background thread: read every new record, then sleep until the next write."""
        while self._active:
            try:
                records = self._read_new()
            except Exception as e:
                if not self._active:
                    break
                print(f"[EasySync-SHM] Read error: {e}")
                records = []
            if records is None:
                print("[EasySync-SHM] Listener fell behind the ring: requesting a full resync")
                self._pos = self._tail()
                self._append({"type": "request_sync", "_src": self.client_id})
                continue
            for meta, raw in records:
                try:
                    message = serialization.loads(meta)
                    if not isinstance(message, dict):
                        continue
                    self.stats["bytes_recv"] += _REC.size + len(meta) + len(raw)
                    self.stats["packets_recv"] += 1
                    payload = bytearray(raw) if message.get("_raw_size") is not None else None
                    self._dispatch_message(message, payload)
                except Exception as e:
                    print(f"[EasySync-SHM] Dropped message: {e}")
            if not records:
                self._bus.wait_update(timeout=0.05)
