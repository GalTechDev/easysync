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
                    self.client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) # Optimization for Zero-Copy payloads

                    # Open UDP socket
                    self._udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self._udp_socket.bind(("", 0))  # Random local port
                    local_udp_port = self._udp_socket.getsockname()[1]
                    self._server_udp_addr = (self.host, self.port + 1)

                    self._send_packet({"type": "auth", "payload": self.auth_payload, "udp_port": local_udp_port})
                    res = self._recv_packet()
                    
                    if not res:
                        print("[EasySync] Connection lost during authentication")
                        self.client_socket.close()
                        continue
                        
                    resp, _ = res

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


    def _send_packet(self, data, payload: bytes | memoryview = None):
        if self.client_socket:
            raw_meta = pickle.dumps(data)
            # Header: total meta length
            header_and_meta = struct.pack(">I", len(raw_meta)) + raw_meta
            try:
                self.stats["bytes_sent"] += len(header_and_meta)
                self.client_socket.sendall(header_and_meta)
                if payload:
                    self.stats["bytes_sent"] += len(payload)
                    self.client_socket.sendall(payload)
                self.stats["packets_sent"] += 1
            except OSError:
                pass

    def _recv_n_bytes(self, n, out_buffer=None):
        """Read exactly n bytes from the socket.
        If out_buffer (bytearray/memoryview) is provided, read directly into it.
        """
        if out_buffer is not None:
            view = out_buffer
            pos = 0
            while pos < n:
                try:
                    read_size = self.client_socket.recv_into(view[pos:], n - pos)
                    if read_size == 0: return False
                    self.stats["bytes_recv"] += read_size
                    pos += read_size
                except OSError: return False
            return True

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
        meta_length = struct.unpack(">I", header)[0]
        raw_meta = self._recv_n_bytes(meta_length)
        if not raw_meta:
            return None
        meta = pickle.loads(raw_meta)
        
        payload = None
        if "_raw_size" in meta:
            # We don't read the payload here yet. 
            # We defer it to _dispatch_message so we can potentially use recv_into.
            pass
            
        return meta, payload

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
            
            # --- Zero-Copy Detection ---
            # Checks if value is a raw-compatible buffer (SHM, NumPy, mmap)
            is_raw = False
            raw_payload = None
            
            # 1. Direct buffer protocol supporters
            if isinstance(value, (bytes, bytearray, memoryview)):
                is_raw = True
                raw_payload = memoryview(value)
            # 2. NumPy arrays (Zero-copy via memoryview)
            elif type(value).__name__ == "ndarray" and hasattr(value, "data"):
                is_raw = True
                raw_payload = value.data # memoryview of array
            # 3. EasySHM objects: check for ._data.buf (mmap)
            elif hasattr(value, "_data") and hasattr(value._data, "buf"):
                is_raw = True
                raw_payload = memoryview(value._data.buf)
            # 4. Direct mmap objects
            elif hasattr(value, "read") and hasattr(value, "seek") and type(value).__name__ == "mmap":
                is_raw = True
                raw_payload = memoryview(value)
            
            if is_raw:
                packet["_raw_size"] = len(raw_payload)
                # Ensure we don't accidentally pickle the buffer source if it's unpicklable
                # We only send the metadata header
                self._send_packet(packet, payload=raw_payload)
                return

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

                # Full encode
                encoded = codec_inst.encode(value)
                packet["_codec"] = codec_name
                self._delta_sent[cache_key] = value
                
                # --- Codec Raw Payload Support ---
                # Codec can return (metadata, raw_source) to trigger zero-copy
                if isinstance(encoded, tuple) and len(encoded) == 2:
                    meta_val, raw_src = encoded
                    packet["value"] = meta_val
                    
                    # Detect raw source
                    raw_payload = None
                    if isinstance(raw_src, (bytes, bytearray, memoryview)):
                        raw_payload = memoryview(raw_src)
                    elif hasattr(raw_src, "_data") and hasattr(raw_src._data, "buf"):
                        raw_payload = memoryview(raw_src._data.buf)
                    
                    if raw_payload is not None:
                        packet["_raw_size"] = len(raw_payload)
                        self._send_packet(packet, payload=raw_payload)
                        return
                
                # Standard codec result
                packet["value"] = encoded
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
                self._dispatch_message(message, None)
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

    def register_codec(self, name, codec_instance):
        from easysync.codecs import register_codec
        register_codec(name, codec_instance)

    def _dispatch_message(self, message, payload, reader=None):
        """Shared logic for handling incoming messages (TCP or UDP)."""
        msg_type = message.get("type")
        if msg_type == "update":
            oid = message.get("object_id")
            attr = message.get("attr_name")
            raw_size = message.get("_raw_size")
            raw_payload = None
            if raw_size is not None and reader:
                # Value was sent as raw binary
                raw_payload = bytearray(raw_size)
                self._recv_n_bytes(raw_size, out_buffer=memoryview(raw_payload))

            # Decode codec-encoded values
            if "_codec" in message:
                from easysync.codecs import get_codec
                codec_name = message["_codec"]
                c = get_codec(codec_name)
                if c:
                    cache_key = (oid, attr)
                    if message.get("_delta"):
                        # Delta decode
                        current = self._delta_received.get(cache_key)
                        if current is not None:
                            message["value"] = c.decode_delta(current, message["value"])
                        else:
                            message["value"] = c.decode(message["value"], raw_payload=raw_payload)
                    else:
                        # Full decode - Pass raw_payload if we have one
                        message["value"] = c.decode(message["value"], raw_payload=raw_payload)

                    self._delta_received[cache_key] = message["value"]
                else:
                    # FALLBACK: If codec is missing but we have raw data, use raw data
                    if raw_payload is not None:
                        message["value"] = raw_payload
                del message["_codec"]
                if "_delta" in message:
                    del message["_delta"]
            
            elif raw_payload is not None:
                message["value"] = raw_payload

            if oid:
                if oid in self.callbacks:
                    self.callbacks[oid](message)
                else:
                    if attr:
                        self._unclaimed_updates.setdefault(oid, {})[attr] = message
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
                res = self._recv_packet()
                if res is None:
                    raise ConnectionError("Server disconnected")
                
                message, payload = res
                # We pass 'self' as the reader to allow deferred raw payload reading
                self._dispatch_message(message, payload, reader=self)

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
