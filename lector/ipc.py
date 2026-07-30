import asyncio
import json
from pathlib import Path


async def serve(path: Path, handler) -> asyncio.AbstractServer:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)

    async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not line:
                return
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                resp = {"ok": False, "error": "bad json"}
            else:
                try:
                    resp = await handler(req)
                except Exception as e:  # noqa: BLE001 — daemon must not die on a bad request
                    resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
        except asyncio.TimeoutError:
            pass
        finally:
            writer.close()

    return await asyncio.start_unix_server(on_client, path=str(path))


def request(sock_path: Path, cmd: str, timeout: float = 10.0, **args) -> dict:
    """Synchronous client used by lectorctl."""
    import socket

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(sock_path))
        sock.sendall((json.dumps({"cmd": cmd, **args}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            data = sock.recv(65536)
            if not data:
                break
            buf += data
    return json.loads(buf) if buf.strip() else {"ok": False, "error": "empty reply"}
