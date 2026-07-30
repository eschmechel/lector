import asyncio
import json
from pathlib import Path


class Player:
    """Thin async client around an mpv --idle instance via its JSON IPC socket."""

    def __init__(self, socket_path: Path):
        self.socket_path = socket_path
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._req_id = 0
        self._reader_task: asyncio.Task | None = None
        self.producer_done = True

    async def start(self) -> None:
        if self._proc and self._proc.returncode is None:
            return
        self.socket_path.unlink(missing_ok=True)
        self._proc = await asyncio.create_subprocess_exec(
            "mpv", "--idle=yes", "--no-video", "--no-terminal",
            f"--input-ipc-server={self.socket_path}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(
                    str(self.socket_path)
                )
                break
            except (ConnectionRefusedError, FileNotFoundError):
                await asyncio.sleep(0.1)
        else:
            raise RuntimeError("mpv IPC socket never came up")
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self._reader
        while True:
            line = await self._reader.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            req_id = msg.get("request_id")
            if req_id in self._pending:
                self._pending.pop(req_id).set_result(msg)

    async def cmd(self, *args):
        assert self._writer
        self._req_id += 1
        req_id = self._req_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        self._writer.write(
            (json.dumps({"command": list(args), "request_id": req_id}) + "\n").encode()
        )
        await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout=5)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return None

    async def get(self, prop: str):
        resp = await self.cmd("get_property", prop)
        return resp.get("data") if resp and resp.get("error") == "success" else None

    async def enqueue(self, path: Path) -> None:
        await self.cmd("loadfile", str(path), "append-play")

    async def toggle_pause(self) -> None:
        await self.cmd("cycle", "pause")

    async def stop(self) -> None:
        self.producer_done = True
        await self.cmd("stop")

    async def is_paused(self) -> bool:
        return bool(await self.get("pause"))

    async def wait_done(self, poll: float = 0.3) -> None:
        """Return once mpv is idle AND the producer has enqueued everything."""
        while True:
            idle = await self.get("idle-active")
            if idle and self.producer_done:
                return
            await asyncio.sleep(poll)

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._writer:
            self._writer.close()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self._proc.kill()
