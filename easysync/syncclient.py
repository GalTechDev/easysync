import socket
import threading
import pickle
import struct


class SyncClient:
    """TCP client that connects to a SyncServer to synchronize objects."""

    def __init__(self, host="localhost", port=5000, auth_payload=None):
        self.host = host
        self.port = port
        self.auth_payload = auth_payload
        self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connected = False
        self.callbacks = {}
        self.on_sync_request_callback = None

    def connect(self):
        try:
            self.client_socket.connect((self.host, self.port))

            self._send_packet({"type": "auth", "payload": self.auth_payload})
            resp = self._recv_packet()
            if not resp:
                print("[EasySync] Connection lost during authentication")
                return

            if resp.get("type") == "auth_reject":
                print("[EasySync] Authentication rejected by server")
                return

            self.connected = True
            print(f"[EasySync] Connected to {self.host}:{self.port}")
            self._send_packet({"type": "request_sync"})

            t = threading.Thread(target=self.receive_loop, daemon=True)
            t.start()
        except Exception as e:
            print(f"[EasySync] Connection failed: {e}")

    def _send_packet(self, data):
        if self.client_socket:
            raw = pickle.dumps(data)
            self.client_socket.sendall(struct.pack(">I", len(raw)) + raw)

    def _recv_n_bytes(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.client_socket.recv(n - len(buf))
            if not chunk:
                return None
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

    def send_update(self, object_id, attr_name, value):
        if not self.connected:
            return
        try:
            self._send_packet({
                "type": "update",
                "object_id": object_id,
                "attr_name": attr_name,
                "value": value,
            })
        except Exception as e:
            print(f"[EasySync] Send error: {e}")

    def register_callback(self, object_id, callback):
        self.callbacks[object_id] = callback

    def receive_loop(self):
        while self.connected:
            try:
                message = self._recv_packet()
                if message is None:
                    print("[EasySync] Disconnected from server")
                    self.connected = False
                    break

                msg_type = message.get("type")
                if msg_type == "update":
                    oid = message.get("object_id")
                    if oid and oid in self.callbacks:
                        self.callbacks[oid](message)
                elif msg_type == "request_sync":
                    if self.on_sync_request_callback:
                        self.on_sync_request_callback()

            except Exception as e:
                print(f"[EasySync] Network error: {e}")
                self.connected = False
                break
