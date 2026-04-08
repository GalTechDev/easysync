import asyncio
import pickle
import struct


class SyncServer:
    """Async TCP+UDP server that relays state updates between all connected clients.

    TCP is used for reliable updates (default).
    UDP is used for low-latency, fire-and-forget updates.
    The UDP relay runs on port+1 automatically.
    """

    MAX_UDP_PAYLOAD = 60000  # Safe limit for UDP datagrams

    def __init__(self, host="0.0.0.0", port=5000):
        self.host = host
        self.port = port
        self.clients: list[asyncio.StreamWriter] = []
        self.udp_clients: dict[asyncio.StreamWriter, tuple] = {}  # writer -> (ip, udp_port)
        self.auth_handler = None
        self._server = None
        self._udp_transport = None

    def on_auth(self, func):
        self.auth_handler = func
        return func

    # -------- TCP --------

    async def _send_packet(self, writer: asyncio.StreamWriter, data: dict):
        raw = pickle.dumps(data)
        writer.write(struct.pack(">I", len(raw)) + raw)
        await writer.drain()

    async def _recv_packet(self, reader: asyncio.StreamReader) -> dict | None:
        header = await reader.readexactly(4)
        length = struct.unpack(">I", header)[0]
        raw = await reader.readexactly(length)
        return pickle.loads(raw)

    async def _broadcast(self, message: dict, sender: asyncio.StreamWriter = None):
        raw = pickle.dumps(message)
        frame = struct.pack(">I", len(raw)) + raw

        dead = []
        for client in self.clients:
            if client is sender:
                continue
            try:
                client.write(frame)
                await client.drain()
            except (ConnectionError, OSError):
                dead.append(client)

        for client in dead:
            self.clients.remove(client)
            self.udp_clients.pop(client, None)
            client.close()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info("peername")
        print(f"[EasySync] New connection: {addr}")

        try:
            message = await self._recv_packet(reader)
            if not message:
                return

            if message.get("type") == "auth":
                if self.auth_handler:
                    if not self.auth_handler(addr, message.get("payload")):
                        await self._send_packet(writer, {"type": "auth_reject"})
                        return
                await self._send_packet(writer, {"type": "auth_ok"})

                # Store UDP address if client provided it
                udp_port = message.get("udp_port")
                if udp_port:
                    self.udp_clients[writer] = (addr[0], udp_port)
            else:
                if self.auth_handler:
                    return
                await self._broadcast(message, sender=writer)

            self.clients.append(writer)

            while True:
                message = await self._recv_packet(reader)
                if not message:
                    break
                
                if message.get("type") == "ping":
                    await self._send_packet(writer, {"type": "pong"})
                    continue

                await self._broadcast(message, sender=writer)

        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        except Exception as e:
            print(f"[EasySync] Client error {addr}: {e}")
        finally:
            print(f"[EasySync] Disconnected: {addr}")
            if writer in self.clients:
                self.clients.remove(writer)
            self.udp_clients.pop(writer, None)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    # -------- UDP --------

    def _udp_broadcast(self, data: bytes, sender_addr: tuple):
        """Relay a UDP datagram to all other clients."""
        if not self._udp_transport:
            return
        for writer, client_udp_addr in list(self.udp_clients.items()):
            if client_udp_addr == sender_addr:
                continue
            try:
                self._udp_transport.sendto(data, client_udp_addr)
            except Exception:
                pass

    # -------- Startup --------

    async def start(self):
        # Start TCP server
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )

        # Start UDP relay
        loop = asyncio.get_running_loop()

        server_ref = self

        class UDPRelay(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                server_ref._udp_transport = transport

            def datagram_received(self, data, addr):
                server_ref._udp_broadcast(data, addr)

        udp_port = self.port + 1
        await loop.create_datagram_endpoint(UDPRelay, local_addr=(self.host, udp_port))

        print(f"[EasySync] Server started on {self.host}:{self.port} (TCP) + {udp_port} (UDP)")
        async with self._server:
            await self._server.serve_forever()

    def start_thread(self):
        """Run the async server in a dedicated thread with its own event loop."""
        import threading

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.start())

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    async def stop(self):
        for client in list(self.clients):
            try:
                client.close()
            except Exception:
                pass
        if self._udp_transport:
            self._udp_transport.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()


if __name__ == "__main__":
    server = SyncServer()
    asyncio.run(server.start())
