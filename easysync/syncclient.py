import socket
import struct
import threading
import time

from easysync import serialization
from easysync._wire import DeltaState, NeedFullValue, decode_update, encode_update, new_client_id


class SyncClient:
    """TCP+UDP client that connects to a SyncServer to synchronize objects."""

    MAX_UDP_PAYLOAD = 60000  # Auto-fallback to TCP above this

    def __init__(
        self,
        host="localhost",
        port=5000,
        auth_payload=None,
        auto_reconnect=True,
        auto_resync=True,
        sync_new_client=True,
    ):
        self.host = host
        self.port = port
        self.auth_payload = auth_payload
        self.auto_reconnect = auto_reconnect
        self.auto_resync = auto_resync
        self.sync_new_client = sync_new_client

        self.client_socket = None
        self._rfile = None
        self._udp_socket = None
        self._server_udp_addr = None
        self.connected = False
        self.auth_rejected = False
        self.callbacks = {}
        self._unclaimed_updates = {}
        self.on_sync_request_callback = None
        self._is_first_connection = True
        self._closing = False
        self._connected_event = threading.Event()
        self._manager = None

        # One sendall() sequence at a time: frames from concurrent threads must not interleave
        self._send_lock = threading.Lock()

        self.client_id = new_client_id()
        self._delta = DeltaState()
        self._udp_seq = {}       # (object_id, attr) -> last sequence number sent over UDP
        self._udp_seen = {}      # (sender, object_id, attr) -> last sequence number received

        # Telemetry
        self.stats = {
            "bytes_sent": 0,
            "bytes_recv": 0,
            "packets_sent": 0,
            "packets_recv": 0,
            "latency_ms": 0,
        }
        self._last_ping_time = 0

    # ------------------------------------------------------------------ connection

    def connect(self, timeout=5.0):
        """Start the connection manager in a background thread.

        Waits up to `timeout` seconds for the connection (0 = don't wait).
        Returns True once connected; on timeout the client keeps retrying in
        the background (if auto_reconnect) and updates are dropped meanwhile.
        """
        if self._manager is None or not self._manager.is_alive():
            self._closing = False
            self._manager = threading.Thread(target=self._connection_manager, daemon=True)
            self._manager.start()
        if timeout:
            deadline = time.time() + timeout
            while not self.connected and not self.auth_rejected and time.time() < deadline:
                self._connected_event.wait(0.05)
            if not self.connected and not self.auth_rejected:
                print(f"[EasySync] Not connected to {self.host}:{self.port} after {timeout}s, retrying in background")
        return self.connected

    def close(self):
        """Disconnect and stop reconnecting."""
        self._closing = True
        self._disconnect()

    def _disconnect(self):
        self.connected = False
        self._connected_event.clear()
        # Only sockets here: the buffered reader belongs to the receive thread
        # (closing it from another thread would wait for its blocked read forever)
        if self.client_socket:
            try:
                self.client_socket.shutdown(socket.SHUT_RDWR)  # wakes a blocked read
            except OSError:
                pass
        for sock in (self.client_socket, self._udp_socket):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass

    def _connection_manager(self):
        """Continuously manages connection state and retry loops if auto_reconnect is enabled."""
        while not self._closing:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=5.0)
                sock.settimeout(None)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.client_socket = sock
                # Buffered reads: one recv() usually brings several small messages
                self._rfile = sock.makefile("rb", buffering=256 * 1024)
                server_ip = sock.getpeername()[0]

                # UDP socket on the same interface as the TCP connection
                self._udp_socket = socket.socket(sock.family, socket.SOCK_DGRAM)
                self._udp_socket.bind((sock.getsockname()[0], 0))
                local_udp_port = self._udp_socket.getsockname()[1]
                self._server_udp_addr = (server_ip, self.port + 1)

                self._send_packet({"type": "auth", "payload": self.auth_payload, "udp_port": local_udp_port})
                res = self._recv_packet()
                if not res:
                    raise ConnectionError("connection lost during authentication")
                resp, _ = res
                if resp.get("type") == "auth_reject":
                    print("[EasySync] Authentication rejected by server")
                    self.auth_rejected = True
                    self._disconnect()
                    return  # Fatal error, don't auto-reconnect
                if resp.get("type") != "auth_ok":
                    raise ConnectionError("unexpected handshake response")

                self.connected = True
                self._connected_event.set()
                print(f"[EasySync] Connected to {self.host}:{self.port}")

                # Ask peers for the current state
                if (self._is_first_connection and self.sync_new_client) or \
                        (not self._is_first_connection and self.auto_resync):
                    self._send_packet({"type": "request_sync"})
                self._is_first_connection = False

                threading.Thread(target=self._udp_receive_loop, daemon=True).start()
                self.receive_loop()  # blocks until disconnection
            except Exception as e:
                if self._is_first_connection and not self._closing:
                    print(f"[EasySync] Connection failed: {e}")
                self._disconnect()
            finally:
                if self._rfile is not None:
                    try:
                        self._rfile.close()
                    except (OSError, ValueError):
                        pass
                    self._rfile = None

            if not self.auto_reconnect or self._closing:
                break
            time.sleep(2)

    # ------------------------------------------------------------------ TCP framing

    def _send_packet(self, data, payload=None):
        sock = self.client_socket
        if not sock:
            return
        raw_meta = serialization.dumps(data)
        frame = struct.pack(">I", len(raw_meta)) + raw_meta
        try:
            with self._send_lock:
                sock.sendall(frame)
                if payload is not None and len(payload):
                    sock.sendall(payload)
            self.stats["bytes_sent"] += len(frame) + (len(payload) if payload is not None else 0)
            self.stats["packets_sent"] += 1
        except OSError:
            pass

    def _recv_into(self, buf):
        """Fill buf from the (buffered) socket. False on disconnection."""
        view = memoryview(buf)
        pos, n = 0, len(buf)
        while pos < n:
            read = self._rfile.readinto(view[pos:])
            if not read:
                return False
            pos += read
        self.stats["bytes_recv"] += n
        return True

    def _recv_packet(self):
        """Read one frame: (metadata dict, raw payload bytearray or None). None on disconnect."""
        try:
            header = bytearray(4)
            if not self._recv_into(header):
                return None
            raw_meta = bytearray(struct.unpack(">I", header)[0])
            if not self._recv_into(raw_meta):
                return None
            try:
                meta = serialization.loads(raw_meta)
            except serialization.UnsafeTypeError:
                # Consume the raw payload so the stream stays aligned, then report
                raw_size = serialization.peek(raw_meta).get("_raw_size")
                if raw_size is not None and not self._recv_into(bytearray(int(raw_size))):
                    return None
                raise
            if not isinstance(meta, dict):
                raise ValueError("malformed message")
            payload = None
            raw_size = meta.get("_raw_size")
            if raw_size is not None:
                payload = bytearray(int(raw_size))
                if not self._recv_into(payload):
                    return None
            self.stats["packets_recv"] += 1
            return meta, payload
        except OSError:
            return None

    # ------------------------------------------------------------------ sending

    def send_update(self, object_id, attr_name, value, transport="tcp"):
        if not self.connected:
            return
        try:
            packet, raw = encode_update(self._delta, self.client_id, object_id, attr_name, value)
            if transport == "udp":
                self._send_udp(packet, raw)
            else:
                self._send_packet(packet, raw)
        except Exception as e:
            print(f"[EasySync] Send error: {e}")

    def _send_udp(self, packet, raw=None):
        """Send an update of a UDP object.

        Deltas and values too big for a datagram go over TCP: a lost datagram
        would break every following delta. A full value sent as a datagram may
        be lost, so it is not kept as a delta base. TCP and UDP share one
        sequence number so a late datagram never overwrites a newer value.
        """
        key = (packet["object_id"], packet["attr_name"])
        seq = self._udp_seq.get(key, 0) + 1
        self._udp_seq[key] = seq
        packet["_useq"] = seq
        if packet.get("_delta") or not self._udp_socket or not self._server_udp_addr:
            self._send_packet(packet, raw)
            return
        if raw is not None:
            if raw.nbytes > self.MAX_UDP_PAYLOAD:
                self._send_packet(packet, raw)
                return
            packet = dict(packet, _raw_inline=bytes(raw))
            packet.pop("_raw_size", None)
        payload = serialization.dumps(packet)
        if len(payload) > self.MAX_UDP_PAYLOAD:
            packet.pop("_raw_inline", None)
            if raw is not None:
                packet["_raw_size"] = raw.nbytes
            self._send_packet(packet, raw)
            return
        self._delta.forget_sent(key)
        try:
            self._udp_socket.sendto(payload, self._server_udp_addr)
            self.stats["bytes_sent"] += len(payload)
            self.stats["packets_sent"] += 1
        except OSError:
            pass

    # ------------------------------------------------------------------ receiving

    def _udp_receive_loop(self):
        """Background loop that listens for incoming UDP datagrams."""
        sock = self._udp_socket
        if not sock:
            return
        sock.settimeout(1.0)
        while self.connected and sock is self._udp_socket:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if addr[:2] != self._server_udp_addr:
                continue  # only the server relays datagrams to us
            try:
                message = serialization.loads(data)
                if not isinstance(message, dict):
                    continue
                self.stats["bytes_recv"] += len(data)
                self.stats["packets_recv"] += 1
                raw = message.pop("_raw_inline", None)
                if raw is not None:
                    message["_raw_size"] = len(raw)
                    raw = bytearray(raw)
                self._dispatch_message(message, raw)
            except Exception as e:
                print(f"[EasySync] Dropped UDP message: {e}")

    def register_callback(self, object_id, callback):
        self.callbacks[object_id] = callback
        if object_id in self._unclaimed_updates:
            for msg in self._unclaimed_updates[object_id].values():
                callback(msg)
            del self._unclaimed_updates[object_id]

    def register_codec(self, name, codec_instance):
        from easysync.codecs import register_codec
        register_codec(name, codec_instance)

    def _is_stale_udp(self, message):
        seq = message.get("_useq")
        if seq is None:
            return False
        key = (message.get("_src"), message.get("object_id"), message.get("attr_name"))
        if seq <= self._udp_seen.get(key, 0):
            return True  # late or duplicated datagram
        self._udp_seen[key] = seq
        return False

    def _dispatch_message(self, message, payload=None):
        """Shared logic for handling incoming messages (TCP or UDP)."""
        msg_type = message.get("type")
        if msg_type == "update":
            if self._is_stale_udp(message):
                return
            try:
                decode_update(self._delta, message, payload)
            except NeedFullValue:
                # We missed the base of this delta: ask its sender for the full value
                self._send_packet({"type": "delta_nack", "_target": message.get("_src"),
                                   "object_id": message.get("object_id"), "attr_name": message.get("attr_name")})
                return
            oid = message.get("object_id")
            attr = message.get("attr_name")
            if oid:
                if oid in self.callbacks:
                    self.callbacks[oid](message)
                elif attr:
                    self._unclaimed_updates.setdefault(oid, {})[attr] = message
        elif msg_type == "request_sync":
            if self.on_sync_request_callback:
                self.on_sync_request_callback()
        elif msg_type == "delta_nack":
            if message.get("_target") == self.client_id:
                self._resend_full(message.get("object_id"), message.get("attr_name"))
        elif msg_type == "pong":
            if self._last_ping_time:
                self.stats["latency_ms"] = round((time.perf_counter() - self._last_ping_time) * 1000, 3)

    def _resend_full(self, object_id, attr_name):
        key = (object_id, attr_name)
        value = self._delta.sent.get(key)
        if value is not None:
            self._delta.forget_sent(key)
            self.send_update(object_id, attr_name, value)

    def ping(self):
        """Send a ping to the server; stats["latency_ms"] holds the round trip once the pong arrives."""
        if not self.connected:
            return
        self._last_ping_time = time.perf_counter()
        self._send_packet({"type": "ping"})

    def receive_loop(self):
        while self.connected:
            try:
                res = self._recv_packet()
                if res is None:
                    raise ConnectionError("Server disconnected")
            except serialization.UnsafeTypeError as e:
                print(f"[EasySync] Dropped message: {e}")
                continue
            except Exception as e:
                if not self._closing:
                    print(f"[EasySync] Disconnected from server: {e}")
                self._disconnect()
                break
            # The frame was fully read: a bad value or a failing callback must not
            # tear the connection down
            try:
                self._dispatch_message(*res)
            except Exception as e:
                print(f"[EasySync] Error while applying an update: {e}")
