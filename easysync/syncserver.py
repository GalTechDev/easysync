import asyncio
import struct
import threading

from easysync import serialization


class SyncServer:
    """Async TCP+UDP server that relays state updates between all connected clients.

    TCP is used for reliable updates (default).
    UDP is used for low-latency, fire-and-forget updates.
    The UDP relay runs on port+1 automatically.

    The server never deserializes the content of updates: it only reads the
    routing fields of each message (without running any code from it) and
    forwards the original bytes.

    Args:
        host, port:         Listening address (UDP uses port + 1).
        max_message_size:   Largest accepted message in bytes (metadata + payload).
                            Bigger messages close the connection.
        send_queue_size:    Messages buffered per client. When a client falls this
                            far behind, senders wait for it (TCP never drops updates)...
        slow_client_timeout: ...for at most this many seconds: a client whose queue
                            stays full longer is disconnected, so one stuck client
                            cannot freeze the others.
    """

    MAX_UDP_PAYLOAD = 60000  # Safe limit for UDP datagrams
    MAX_HANDSHAKE_SIZE = 64 * 1024  # First message (auth) size limit
    # Above this many buffered bytes, a client's messages go through its queue.
    # Kept small: asyncio's get_write_buffer_size() walks every buffered chunk.
    WRITE_BUFFER_LIMIT = 64 * 1024

    def __init__(self, host="0.0.0.0", port=5000, max_message_size=512 * 1024 * 1024, send_queue_size=4096,
                 slow_client_timeout=10.0):
        self.host = host
        self.port = port
        self.max_message_size = max_message_size
        self.send_queue_size = send_queue_size
        self.slow_client_timeout = slow_client_timeout
        self.clients: list[asyncio.StreamWriter] = []
        self.udp_clients: dict[asyncio.StreamWriter, tuple] = {}  # writer -> (ip, udp_port)
        self._queues: dict[asyncio.StreamWriter, asyncio.Queue] = {}
        self._writer_tasks: set[asyncio.Task] = set()
        self.auth_handler = None
        self._server = None
        self._udp_transport = None
        self._loop = None

    def on_auth(self, func):
        """Decorator: func(addr, payload) -> bool decides whether a client may join."""
        self.auth_handler = func
        return func

    def register_codec(self, name, codec_instance):
        from easysync.codecs import register_codec
        register_codec(name, codec_instance)

    # -------- TCP --------

    @staticmethod
    def _frame(message: dict) -> bytes:
        raw_meta = serialization.dumps(message)
        return struct.pack(">I", len(raw_meta)) + raw_meta

    async def _send_packet(self, writer: asyncio.StreamWriter, data: dict):
        writer.write(self._frame(data))
        await writer.drain()

    async def _recv_frame(self, reader: asyncio.StreamReader, limit: int):
        """Read one message. Returns (routing fields, original frame chunks) or None."""
        try:
            header = await reader.readexactly(4)
            meta_length = struct.unpack(">I", header)[0]
            if meta_length > limit:
                return None
            raw_meta = await reader.readexactly(meta_length)
            meta = serialization.peek(raw_meta)
            if not isinstance(meta, dict):
                return None
            chunks = [header + raw_meta]
            raw_size = meta.get("_raw_size")
            if raw_size is not None:
                if not isinstance(raw_size, int) or raw_size < 0 or meta_length + raw_size > limit:
                    return None
                chunks.append(await reader.readexactly(raw_size))
            return meta, chunks
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            return None
        except Exception:
            return None  # malformed metadata

    async def _writer_task(self, writer: asyncio.StreamWriter, queue: asyncio.Queue):
        """Per-client sender: a slow client only delays its own queue."""
        try:
            while True:
                batch = [await queue.get()]
                # Write everything already queued, then wait for the socket once
                while not queue.empty() and len(batch) < 1024:
                    batch.append(queue.get_nowait())
                stop = None in batch
                data = []
                for chunks in batch:
                    if chunks is None:
                        break
                    data.extend(chunks)
                writer.writelines(data)  # one buffer entry for the whole batch
                await writer.drain()
                if stop:
                    break
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def _broadcast(self, chunks, sender: asyncio.StreamWriter = None):
        for client in list(self.clients):
            if client is sender:
                continue
            queue = self._queues.get(client)
            if queue is None:
                continue
            # Fast path: nothing pending for this client and its socket buffer is
            # not backed up -> write now (order is kept: the queue is empty)
            transport = client.transport
            if queue.empty() and not transport.is_closing() and \
                    transport.get_write_buffer_size() < self.WRITE_BUFFER_LIMIT:
                client.writelines(chunks)
                continue
            try:
                queue.put_nowait(chunks)
            except asyncio.QueueFull:
                try:
                    await asyncio.wait_for(queue.put(chunks), self.slow_client_timeout)
                except asyncio.TimeoutError:
                    print(f"[EasySync] Dropping slow client {client.get_extra_info('peername')}")
                    self._drop(client)

    def _drop(self, writer: asyncio.StreamWriter):
        if writer in self.clients:
            self.clients.remove(writer)
        self.udp_clients.pop(writer, None)
        self._queues.pop(writer, None)
        writer.close()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info("peername")
        print(f"[EasySync] New connection: {addr}")
        writer_task = None

        try:
            # The first message must be the handshake, whatever the auth settings
            res = await self._recv_frame(reader, self.MAX_HANDSHAKE_SIZE)
            if not res or res[0].get("type") != "auth":
                return
            hello = serialization.loads(res[1][0][4:])  # restricted: plain data only
            if self.auth_handler:
                try:
                    accepted = self.auth_handler(addr, hello.get("payload"))
                except Exception as e:
                    print(f"[EasySync] Auth handler error for {addr}: {e}")
                    accepted = False
                if not accepted:
                    await self._send_packet(writer, {"type": "auth_reject"})
                    return
            await self._send_packet(writer, {"type": "auth_ok"})

            udp_port = hello.get("udp_port")
            if isinstance(udp_port, int) and 0 < udp_port < 65536:
                self.udp_clients[writer] = (addr[0], udp_port)

            queue = asyncio.Queue(maxsize=self.send_queue_size)
            self._queues[writer] = queue
            writer_task = asyncio.create_task(self._writer_task(writer, queue))
            self._writer_tasks.add(writer_task)
            writer_task.add_done_callback(self._writer_tasks.discard)
            self.clients.append(writer)

            while True:
                res = await self._recv_frame(reader, self.max_message_size)
                if not res:
                    break
                meta, chunks = res
                if meta.get("type") == "ping":
                    await queue.put([self._frame({"type": "pong"})])
                    continue
                await self._broadcast(chunks, sender=writer)

        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        except Exception as e:
            print(f"[EasySync] Client error {addr}: {e}")
        finally:
            print(f"[EasySync] Disconnected: {addr}")
            if writer in self.clients:
                self.clients.remove(writer)
            self.udp_clients.pop(writer, None)
            queue = self._queues.pop(writer, None)
            if writer_task is not None:
                # Let the writer flush what is queued, then stop it
                try:
                    queue.put_nowait(None)
                except (asyncio.QueueFull, AttributeError):  # full, or already dropped
                    writer_task.cancel()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    # -------- UDP --------

    def _udp_broadcast(self, data: bytes, sender_addr: tuple):
        """Relay a UDP datagram from an authenticated client to all other clients."""
        if not self._udp_transport:
            return
        registered = list(self.udp_clients.values())
        if sender_addr[:2] not in registered:
            return  # unknown source: not an authenticated client
        for client_udp_addr in registered:
            if client_udp_addr == sender_addr[:2]:
                continue
            try:
                self._udp_transport.sendto(data, client_udp_addr)
            except Exception:
                pass

    # -------- Startup --------

    async def start(self, ready: threading.Event = None):
        self._loop = asyncio.get_running_loop()
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)

        server_ref = self

        class UDPRelay(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                server_ref._udp_transport = transport

            def datagram_received(self, data, addr):
                server_ref._udp_broadcast(data, addr)

        udp_port = self.port + 1
        await self._loop.create_datagram_endpoint(UDPRelay, local_addr=(self.host, udp_port))

        print(f"[EasySync] Server started on {self.host}:{self.port} (TCP) + {udp_port} (UDP)")
        if ready is not None:
            ready.set()
        async with self._server:
            await self._server.serve_forever()

    def start_thread(self, timeout=5.0):
        """Run the server in a background thread; returns once it accepts connections."""
        ready = threading.Event()
        errors = []

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.start(ready))
            except asyncio.CancelledError:
                pass  # stopped
            except Exception as e:
                errors.append(e)
                ready.set()
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()

        threading.Thread(target=_run, daemon=True).start()
        ready.wait(timeout)
        if errors:
            raise errors[0]

    async def stop(self):
        for task in list(self._writer_tasks):
            task.cancel()
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
