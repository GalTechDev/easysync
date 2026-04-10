"""
EasySync — SHM Sync Client
============================
A drop-in replacement for SyncClient that uses EasySHM instead of TCP/UDP sockets.
Zero-network, zero-server local IPC for maximum performance.

Usage:
    from easysync import shm_connect, SyncedObject

    client = shm_connect("my_app")

    @SyncedObject(client)
    class State:
        def __init__(self):
            self.score = 0

    state = State()
    state.score = 42  # Propagated via shared memory (zero-copy)
"""

import pickle
import struct
import threading
import time
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from easyshm import EasySHM


# --- Message Bus Layout ---
# The bus is an EasySHM segment used as a simple message slot.
#
# Layout of the DATA region:
#   [0:4]   - msg_length (uint32)  — length of the pickled message
#   [4:4+N] - pickled message bytes
#
# Each write overwrites the previous message. The EasySHM write_seq
# counter serves as the sequence number for change detection.

_BUS_HEADER = struct.Struct("<I")  # 4 bytes: message length


class SHMSyncClient:
    """A SyncClient-compatible class that uses shared memory for IPC.

    This replaces TCP/UDP sockets with an EasySHM message bus.
    All processes using the same cluster_name share state through RAM.
    No SyncServer is needed.
    """

    def __init__(self, cluster_name: str):
        self.cluster_name = cluster_name
        self.connected = False
        self.callbacks = {}
        self._unclaimed_updates = {}
        self.on_sync_request_callback = None

        # Delta caches (same interface as SyncClient)
        self._delta_sent = {}
        self._delta_received = {}

        # Telemetry (compatible with SyncClient.stats)
        self.stats = {
            "bytes_sent": 0,
            "bytes_recv": 0,
            "packets_sent": 0,
            "packets_recv": 0,
            "latency_ms": 0,
        }

        # Unique ID for this client instance to avoid echoing our own messages.
        # id(self) alone is not enough across processes as virtual addresses can overlap.
        self._sender_id = (os.getpid(), id(self))

        # The shared message bus (one per cluster)
        self._bus = EasySHM(f"easysync_{cluster_name}_bus", size=65536, auto_grow=True)
        self._last_seq = self._bus.write_seq

        # Listener thread
        self._active = True
        self._listener = threading.Thread(target=self._listener_loop, daemon=True)

    def connect(self):
        """Activate the SHM client. No network connection needed."""
        self.connected = True
        self._listener.start()
        print(f"[EasySync-SHM] Connected to cluster '{self.cluster_name}' via shared memory")

    def send_update(self, object_id, attr_name, value, transport="shm"):
        """Send an attribute update via the shared memory bus.

        Args:
            object_id:  The synced object identifier (class qualname).
            attr_name:  Name of the attribute that changed.
            value:      The new value.
            transport:  Ignored (always SHM).
        """
        if not self.connected:
            return

        try:
            from easysync.codecs import find_codec

            packet = {
                "type": "update",
                "object_id": object_id,
                "attr_name": attr_name,
                "_sender": self._sender_id,  # Used to filter out our own messages
            }

            result = find_codec(value)
            if result:
                codec_name, codec_inst = result
                cache_key = (object_id, attr_name)

                # Try delta encoding
                if codec_inst.supports_delta():
                    old = self._delta_sent.get(cache_key)
                    if old is not None:
                        delta = codec_inst.encode_delta(old, value)
                        if delta is not None:
                            packet["value"] = delta
                            packet["_codec"] = codec_name
                            packet["_delta"] = True
                            self._delta_sent[cache_key] = value
                            self._write_to_bus(packet)
                            return

                # Full encode
                packet["value"] = codec_inst.encode(value)
                packet["_codec"] = codec_name
                self._delta_sent[cache_key] = value
            else:
                packet["value"] = value

            self._write_to_bus(packet)

        except Exception as e:
            print(f"[EasySync-SHM] Send error: {e}")

    def register_callback(self, object_id, callback):
        """Register a callback for incoming updates on a synced object."""
        self.callbacks[object_id] = callback
        # Deliver any updates that arrived before the callback was registered
        if object_id in self._unclaimed_updates:
            for msg in self._unclaimed_updates[object_id].values():
                callback(msg)
            del self._unclaimed_updates[object_id]

    def ping(self):
        """No-op for SHM (latency is ~0)."""
        self.stats["latency_ms"] = 0

    def close(self):
        """Stop the listener and release resources."""
        self._active = False
        self.connected = False
        if self._bus:
            self._bus.close()
            self._bus = None

    # ------------------------------------------------------------------
    # Internal: Bus I/O
    # ------------------------------------------------------------------

    def _write_to_bus(self, packet):
        """Serialize and write a message to the shared memory bus."""
        raw = pickle.dumps(packet)
        # Write: [4 bytes length][pickled data]
        frame = _BUS_HEADER.pack(len(raw)) + raw
        self._bus.write(frame)
        self.stats["bytes_sent"] += len(frame)
        self.stats["packets_sent"] += 1

    def _read_from_bus(self):
        """Read the latest message from the bus. Returns dict or None."""
        try:
            header_raw = self._bus.read(size=4, offset=0)
            if len(header_raw) < 4:
                return None
            msg_len = _BUS_HEADER.unpack(header_raw)[0]
            if msg_len == 0:
                return None
            raw = self._bus.read(size=msg_len, offset=4)
            self.stats["bytes_recv"] += 4 + len(raw)
            self.stats["packets_recv"] += 1
            return pickle.loads(raw)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Internal: Dispatch
    # ------------------------------------------------------------------

    def _dispatch_message(self, message):
        """Process an incoming message (same logic as SyncClient)."""
        # Ignore our own messages
        if message.get("_sender") == self._sender_id:
            return

        msg_type = message.get("type")

        if msg_type == "update":
            # Decode codec-encoded values
            if "_codec" in message:
                from easysync.codecs import get_codec
                codec_name = message["_codec"]
                c = get_codec(codec_name)
                if c:
                    oid = message.get("object_id", "")
                    attr = message.get("attr_name", "")
                    cache_key = (oid, attr)

                    if message.get("_delta"):
                        current = self._delta_received.get(cache_key)
                        if current is not None:
                            message["value"] = c.decode_delta(current, message["value"])
                        else:
                            message["value"] = c.decode(message["value"])
                    else:
                        message["value"] = c.decode(message["value"])

                    self._delta_received[cache_key] = message["value"]
                del message["_codec"]
                if "_delta" in message:
                    del message["_delta"]

            # Clean up internal fields before dispatching
            if "_sender" in message:
                del message["_sender"]

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

    # ------------------------------------------------------------------
    # Internal: Listener Thread
    # ------------------------------------------------------------------

    def _listener_loop(self):
        """Background thread that polls the SHM bus for new messages.
        
        This loop is 'self-healing': it checks the sequence number 
        periodically even if no hardware signal is received.
        """
        while self._active:
            # 1. Check if there's already an update we missed
            current_seq = self._bus.write_seq
            if current_seq != self._last_seq:
                self._last_seq = current_seq
                message = self._read_from_bus()
                if message:
                    self._dispatch_message(message)
                continue # Re-check immediately in case another write happened

            # 2. Otherwise, wait for the next signal (or 50ms timeout)
            self._bus._signal.wait(timeout_ms=50)

            if not self._active:
                break

