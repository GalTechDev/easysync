import asyncio
import pickle
import struct


class SyncServer:
    """Serveur de synchronisation asynchrone basé sur asyncio.

    Remplace SyncServer pour les environnements à forte charge
    où le threading bloquant devient un goulot d'étranglement.
    Le protocole réseau est identique : SyncClient fonctionne tel quel.
    """

    def __init__(self, host="0.0.0.0", port=5000):
        self.host = host
        self.port = port
        self.clients: list[asyncio.StreamWriter] = []
        self.auth_handler = None
        self._server = None

    def on_auth(self, func):
        self.auth_handler = func
        return func

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
            client.close()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info("peername")
        print(f"[EasySync] Nouvelle connexion : {addr}")

        try:
            # Authentification
            message = await self._recv_packet(reader)
            if not message:
                return

            if message.get("type") == "auth":
                if self.auth_handler:
                    if not self.auth_handler(addr, message.get("payload")):
                        await self._send_packet(writer, {"type": "auth_reject"})
                        return
                await self._send_packet(writer, {"type": "auth_ok"})
            else:
                if self.auth_handler:
                    return
                await self._broadcast(message, sender=writer)

            self.clients.append(writer)

            # Boucle de réception
            while True:
                message = await self._recv_packet(reader)
                if not message:
                    break
                await self._broadcast(message, sender=writer)

        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        except Exception as e:
            print(f"[EasySync] Erreur client {addr} : {e}")
        finally:
            print(f"[EasySync] Déconnexion : {addr}")
            if writer in self.clients:
                self.clients.remove(writer)
            writer.close()

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        print(f"[EasySync] Serveur async démarré sur {self.host}:{self.port}")
        async with self._server:
            await self._server.serve_forever()

    def start_thread(self):
        """Lance le serveur async dans un thread dédié avec sa propre boucle."""
        import threading

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.start())

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()


if __name__ == "__main__":
    server = SyncServer()
    asyncio.run(server.start())
