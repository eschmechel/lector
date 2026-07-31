import asyncio
from pathlib import Path

from .config import DOC_SUFFIXES


async def _run(*cmd: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return None
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    return out.decode(errors="replace")


def as_doc_path(text: str) -> Path | None:
    """A short single-line clipboard payload that names an existing document file."""
    text = text.strip()
    if not text or "\n" in text or len(text) > 500:
        return None
    if text.startswith("file://"):
        from urllib.parse import unquote, urlparse

        text = unquote(urlparse(text).path)
    try:
        p = Path(text).expanduser()
        if p.is_file() and p.suffix.lower() in DOC_SUFFIXES:
            return p
    except OSError:  # e.g. ENAMETOOLONG: a long sentence is not a path
        pass
    return None


async def clipboard() -> str | Path | None:
    types = await _run("wl-paste", "--list-types") or ""
    if "text/uri-list" in types:
        uris = await _run("wl-paste", "-n") or ""
        for line in uris.splitlines():
            p = as_doc_path(line)
            if p:
                return p
        return None
    text = await _run("wl-paste", "-n", "-t", "text")
    if not text or not text.strip():
        return None
    return as_doc_path(text) or text


async def selection() -> str | Path | None:
    text = await _run("wl-paste", "-p", "-n")
    if not text or not text.strip():
        return None
    return as_doc_path(text) or text


async def resolve_source(source: str = "auto", path: str | None = None) -> str | Path | None:
    if path:
        p = Path(path).expanduser()
        return p if p.is_file() else None
    if source == "file":
        return None  # file picking happens client-side (lectorctl fzf picker)
    if source == "clipboard":
        return await clipboard()
    if source == "selection":
        return await selection()
    # auto: highlighted text first (D32), clipboard as fallback
    return await selection() or await clipboard()


class InboxWatcher:
    """Watch the drop folder; surface new documents via an async callback."""

    def __init__(self, directory: Path, loop: asyncio.AbstractEventLoop, on_file):
        self.directory = directory
        self.loop = loop
        self.on_file = on_file
        self._observer = None

    def start(self) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        watcher = self

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                watcher._seen(event)

            def on_moved(self, event):
                event.src_path = event.dest_path
                watcher._seen(event)

        self._observer = Observer()
        self._observer.schedule(Handler(), str(self.directory))
        self._observer.daemon = True
        self._observer.start()

    def _seen(self, event) -> None:
        if getattr(event, "is_directory", False):
            return
        p = Path(event.src_path)
        if p.suffix.lower() not in DOC_SUFFIXES:
            return
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._handle(p)))

    async def _handle(self, p: Path) -> None:
        # Let the copy finish before reading.
        await asyncio.sleep(1.0)
        if p.is_file():
            await self.on_file(p)

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
