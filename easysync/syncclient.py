import socket
import threading
import pickle
import struct


import time

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
        self._udp_socket = None
        self._server_udp_addr = None
        self.connected = False
        self.callbacks = {}
        self._unclaimed_updates = {}
        self.on_sync_request_callback = None
        self._is_first_connection = True
        self._delta_sent = {}      # {(object_id, attr_name): value}
        self._delta_received = {}  # {(object_id, attr_name): value}
        
        # Telemetry
        self.stats = {
            "bytes_sent": 0,
            "bytes_recv": 0,
            "packets_sent": 0,
            "packets_recv": 0,
            "latency_ms": 0,
        }
        self._last_ping_time = 0

    def connect(self):
        """Starts the connection manager gracefully in a background thread."""
        t = threading.Thread(target=self._connection_manager, daemon=True)
        t.start()

    def _connection_manager(self):
        """Continuously manages connection state and retry loops if auto_reconnect is enabled."""
        while True:
            if not self.connected:
                try:
                    self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.client_socket.settimeout(5.0)  # Timeout for connection
                    self.client_socket.connect((self.host, self.port))
                    self.client_socket.settimeout(None) # Remove timeout for blocking mode

                    # Open UDP socket
                    self._udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self._udp_socket.bind(("", 0))  # Random local port
                    local_udp_port = self._udp_socket.getsockname()[1]
                    self._server_udp_addr = (self.host, self.port + 1)

                    self._send_packet({"type": "auth", "payload": self.auth_payload, "udp_port": local_udp_port})
                    resp = self._recv_packet()
                    
                    if not resp:
                        print("[EasySync] Connection lost during authentication")
                        self.client_socket.close()
                        continue

                    if resp.get("type") == "auth_reject":
                        print("[EasySync] Authentication rejected by server")
                        self.client_socket.close()
                        break # Fatal error, don't auto-reconnect

                    self.connected = True
                    print(f"[EasySync] Connected to {self.host}:{self.port}")

                    # Logic for requesting network state
                    if self._is_first_connection and self.sync_new_client:
                        self._send_packet({"type": "request_sync"})
                    elif not self._is_first_connection and self.auto_resync:
                        self._send_packet({"type": "request_sync"})
                    
                    self._is_first_connection = False
                    
                    # Start UDP receive loop in a separate thread
                    udp_t = threading.Thread(target=self._udp_receive_loop, daemon=True)
                    udp_t.start()
                    
                    # Enter the blocking TCP receiving loop
                    self.receive_loop()

                except Exception as e:
                    # Print full exception only if it's the first connection error, otherwise stay silent
                    if self._is_first_connection:
                        print(f"[EasySync] Connection failed: {e}")
                    pass
                
                # If auto_reconnect is false and we got here (after a loop exits or connect fails), stop trying.
                if not self.auto_reconnect:
                    break
                    
            time.sleep(2)


    def _send_packet(self, data):
        if self.client_socket:
            raw = pickle.dumps(data)
            frame = struct.pack(">I", len(raw)) + raw
            try:
                self.stats["bytes_sent"] += len(frame)
                self.stats["packets_sent"] += 1
                self.client_socket.sendall(frame)
            except OSError:
                pass

    def _recv_n_bytes(self, n):
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self.client_socket.recv(n - len(buf))
            except OSError:
                return None
            if not chunk:
                return None
            self.stats["bytes_recv"] += len(chunk)
            buf.extend(chunk)
        return bytes(buf)

    def _recv_packet(self):
        header = self._recv_n_bytes(4)
        if not header:
            return None
        length = struct.unpack(">I", header)[0]
        data = self._recv_n_bytes(length)
        if not data:
            return None
        return pickle.loads(data)

    def send_update(self, object_id, attr_name, value, transport="tcp"):
        if not self.connected:
            return
        try:
            from easysync.codecs import find_codec
            packet = {
                "type": "update",
                "object_id": object_id,
                "attr_name": attr_name,
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
                            if transport == "udp":
                                self._send_udp(packet)
                            else:
                                self._send_packet(packet)
                            return

                # Full encode (no delta or delta not worth it)
                packet["value"] = codec_inst.encode(value)
                packet["_codec"] = codec_name
                self._delta_sent[cache_key] = value
            else:
                packet["value"] = value

            if transport == "udp":
                self._send_udp(packet)
            else:
                self._send_packet(packet)
        except Exception as e:
            print(f"[EasySync] Send error: {e}")

    def _send_udp(self, packet):
        """Send a packet via UDP. Falls back to TCP if payload exceeds limit."""
        if not self._udp_socket or not self._server_udp_addr:
            self._send_packet(packet)
            return
        payload = pickle.dumps(packet)
        if len(payload) > self.MAX_UDP_PAYLOAD:
            # Auto-fallback to TCP for large payloads
            self._send_packet(packet)
            return
        try:
            self.stats["bytes_sent"] += len(payload)
            self.stats["packets_sent"] += 1
            self._udp_socket.sendto(payload, self._server_udp_addr)
        except OSError:
            pass

    def _udp_receive_loop(self):
        """Background loop that listens for incoming UDP datagrams."""
        if not self._udp_socket:
            return
        self._udp_socket.settimeout(1.0)
        while self.connected:
            try:
                data, addr = self._udp_socket.recvfrom(65535)
                self.stats["bytes_recv"] += len(data)
                self.stats["packets_recv"] += 1
                message = pickle.loads(data)
                self._dispatch_message(message)
            except socket.timeout:
                continue
            except Exception:
                if not self.connected:
                    break
                time.sleep(0.01)

    def register_callback(self, object_id, callback):
        self.callbacks[object_id] = callback
        if object_id in self._unclaimed_updates:
            for msg in self._unclaimed_updates[object_id].values():
                callback(msg)
            del self._unclaimed_updates[object_id]

    def _dispatch_message(self, message):
        """Shared logic for handling incoming messages (TCP or UDP)."""
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
                        # Delta decode: apply delta to cached value
                        current = self._delta_received.get(cache_key)
                        if current is not None:
                            message["value"] = c.decode_delta(current, message["value"])
                        else:
                            # No cached value — can't apply delta, skip
                            message["value"] = c.decode(message["value"])
                    else:
                        # Full decode
                        message["value"] = c.decode(message["value"])

                    self._delta_received[cache_key] = message["value"]
                del message["_codec"]
                if "_delta" in message:
                    del message["_delta"]

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

    def ping(self):
        """Send a ping to the server to measure RTT."""
        if not self.connected: return
        self._last_ping_time = time.time()
        self._send_packet({"type": "ping"})

    def receive_loop(self):
        while self.connected:
            try:
                message = self._recv_packet()
                if message is None:
                    raise ConnectionError("Server disconnected")
                self._dispatch_message(message)

            except Exception as e:
                print(f"[EasySync] Disconnected from server: {e}")
                
                self.connected = False
                if self.client_socket:
                    try:
                        self.client_socket.close()
                    except:
                        pass
                if self._udp_socket:
                    try:
                        self._udp_socket.close()
                    except:
                        pass
                break
