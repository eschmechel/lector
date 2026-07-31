import asyncio
import datetime as dt
import os
import re
import shutil
import signal
import sys
import uuid
from pathlib import Path

from . import config as C
from .brain import Brain, BrainUnavailable
from .capture import resolve_source
from .ingest import Doc, load_source
from .ipc import serve
from .notify import ask, notify
from .playback import Player
from .state import StateFile
from .tts_kokoro import KokoroTTS, ModelsMissing, chunk_text


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:40] or "untitled"


class Daemon:
    def __init__(self, cfg: C.Config):
        self.cfg = cfg
        self.state = StateFile(C.STATE_FILE)
        self.tts = KokoroTTS(cfg.models_dir, cfg.voice, cfg.speed)
        self.brain = Brain(cfg)
        self.player: Player | None = None
        self.session: asyncio.Task | None = None
        self.current_doc: Doc | None = None
        self.render_dir = C.RENDER_DIR / "current"
        self._next_event = asyncio.Event()
        self._skip = False
        self._stopping = False
        self._answers: dict[str, asyncio.Future] = {}
        self._fg_task: asyncio.Task | None = None  # in-flight read/brain pipeline

    # ------------------------------------------------------------- dispatch

    async def handle(self, req: dict) -> dict:
        cmd = req.get("cmd")
        if cmd == "read":
            if req.get("smart"):
                asyncio.ensure_future(self.start_brain(
                    "smart", req.get("source", "auto"), req.get("path"),
                    req.get("text"), bool(req.get("cloud"))))
            else:
                asyncio.ensure_future(self.start_read(
                    req.get("source", "auto"), req.get("path"), req.get("text")))
            return {"ok": True, "queued": True}
        if cmd in ("summarize", "annotate"):
            asyncio.ensure_future(self.start_brain(
                cmd, req.get("source", "auto"), req.get("path"),
                req.get("text"), bool(req.get("cloud"))))
            return {"ok": True, "queued": True}
        if cmd == "pause":
            return await self.pause()
        if cmd == "stop":
            await self.stop_session()
            return {"ok": True}
        if cmd == "next":
            return await self.next_section()
        if cmd == "keep":
            return await self.keep()
        if cmd == "status":
            return {"ok": True, **self.state.get()}
        if cmd == "answer":
            fut = self._answers.pop(req.get("id", ""), None)
            if fut and not fut.done():
                fut.set_result(req.get("choice") or None)
            return {"ok": True}
        if cmd in ("ask", "dictate"):
            await notify("Not yet", f"'{cmd}' arrives in a later phase (P3/P4).")
            return {"ok": True, "pending_phase": True}
        return {"ok": False, "error": f"unknown command: {cmd}"}

    # ------------------------------------------------------------- ui ask

    async def ui_ask(self, summary: str, options: list[tuple[str, str]],
                     timeout: float = 120.0) -> str | None:
        """Ask via a floating fzf terminal (dunst actions are unreliable — middle-click
        only in most configs). Returns the chosen key, or None on cancel/timeout."""
        aid = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._answers[aid] = fut
        ctl = Path(sys.argv[0]).with_name("lectorctl")
        term = os.environ.get("TERMINAL") or "kitty"
        name = Path(term).name
        clsflag = "--app-id=lector-menu" if name == "foot" else "--class=lector-menu"
        pairs = [f"{k}={label}" for k, label in options]
        try:
            await asyncio.create_subprocess_exec(
                term, clsflag, str(ctl), "answer", aid, summary, *pairs,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            return await asyncio.wait_for(fut, timeout)
        except (asyncio.TimeoutError, FileNotFoundError):
            return None
        finally:
            self._answers.pop(aid, None)

    # ------------------------------------------------------------- session

    async def _load_doc(self, source: str, path: str | None,
                        text: str | None) -> Doc | None:
        value = text if text and text.strip() else await resolve_source(source, path)
        if value is None:
            await notify("Nothing to read", "Nothing highlighted or in the clipboard.")
            return None
        doc = await asyncio.to_thread(load_source, value)
        if not doc.word_count:
            await notify("Nothing to read", f"No text found in {doc.title!r}.")
            return None
        return doc

    async def start_read(self, source: str = "auto", path: str | None = None,
                         text: str | None = None) -> None:
        try:
            await self.stop_session()
            self._fg_task = asyncio.current_task()
            self.state.set(state="processing")
            doc = await self._load_doc(source, path, text)
            if doc is None:
                self.state.set(state="idle")
                return

            mode = "all"
            words = doc.word_count
            if words > self.cfg.long_doc_words:
                choice = await self.ui_ask(
                    f"{doc.title} — ~{words} words, {len(doc.sections)} sections",
                    [("all", "Read all"), ("sections", "Section by section"),
                     ("summarize", "Summarize instead"), ("cancel", "Cancel")],
                )
                if choice in (None, "cancel"):
                    await notify("Read cancelled", doc.title, timeout_ms=3000)
                    self.state.set(state="idle")
                    return
                if choice == "summarize":
                    await self.start_brain("summarize", doc=doc)
                    return
                mode = choice
            print(f"reading {doc.title!r} (~{words} words, mode={mode})", flush=True)
            await notify("Reading", doc.title, timeout_ms=3000)
            self.session = asyncio.create_task(self._run_session(doc, mode))
        except Exception as e:  # noqa: BLE001
            print(f"read failed: {type(e).__name__}: {e}", flush=True)
            await notify("lector error", str(e), urgency="critical")
            self.state.set(state="idle")

    async def start_brain(self, mode: str, source: str = "auto",
                          path: str | None = None, text: str | None = None,
                          cloud: bool = False, doc: Doc | None = None) -> None:
        verb = {"summarize": "Summarizing", "annotate": "Annotating",
                "smart": "Rewriting for listening"}[mode]
        try:
            await self.stop_session()
            self._fg_task = asyncio.current_task()
            self.state.set(state="processing")
            if doc is None:
                doc = await self._load_doc(source, path, text)
            if doc is None:
                self.state.set(state="idle")
                return
            await notify(verb, doc.title, timeout_ms=4000)
            print(f"{mode}: {doc.title!r} (~{doc.word_count} words, "
                  f"cloud={cloud})", flush=True)
            full_text = "\n\n".join(
                f"## {s.title}\n\n{s.text}" if s.title else s.text
                for s in doc.sections)
            result = await asyncio.to_thread(
                self.brain.run, mode, doc.title, full_text, cloud)
            if not result.strip():
                await notify("lector error", f"{mode} produced no output",
                             urgency="critical")
                self.state.set(state="idle")
                return

            if mode in ("summarize", "annotate"):
                out = await self._save_note(mode, doc.title, result)
                await self._copy_to_clipboard(result)
                await notify(f"{mode.capitalize()} saved (and on clipboard)", str(out))
            if mode in ("summarize", "smart"):
                spoken = load_source(result)
                spoken.title = f"{doc.title} ({mode})"
                self.session = asyncio.create_task(self._run_session(spoken, "all"))
            else:
                self.state.set(state="idle")
        except BrainUnavailable as e:
            print(f"{mode} failed: {e}", flush=True)
            await notify("lector: LLM unavailable", str(e), urgency="critical")
            self.state.set(state="idle")
        except Exception as e:  # noqa: BLE001
            print(f"{mode} failed: {type(e).__name__}: {e}", flush=True)
            await notify("lector error", str(e), urgency="critical")
            self.state.set(state="idle")

    async def _save_note(self, mode: str, title: str, body: str):
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
        out = self.cfg.notes_out_dir / f"{slugify(title)}-{mode}-{stamp}.md"
        model = (self.cfg.llm_cloud_model if self.cfg.llm_provider == "cloud"
                 else self.cfg.llm_local_model)
        out.write_text(f"---\nsource: {title}\nmode: {mode}\nmodel: {model}\n"
                       f"date: {dt.datetime.now():%Y-%m-%d %H:%M}\n---\n\n{body}\n")
        return out

    async def _copy_to_clipboard(self, text: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "wl-copy", stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.communicate(text.encode())

    async def _run_session(self, doc: Doc, mode: str) -> None:
        self.current_doc = doc
        shutil.rmtree(self.render_dir, ignore_errors=True)
        self.render_dir.mkdir(parents=True, exist_ok=True)
        try:
            if self.player is None:
                self.player = Player(C.MPV_SOCKET)
            await self.player.start()
            total = len(doc.sections)
            for i, sec in enumerate(doc.sections):
                self._skip = False
                self.state.set(state="playing", title=doc.title,
                               section=i + 1, sections=total, section_title=sec.title)
                await self._play_section(i, sec, announce_title=total > 1)
                if self._stopping:
                    return
                if mode == "sections" and i < total - 1:
                    nxt = doc.sections[i + 1].title
                    self.state.set(state="section-wait", title=doc.title,
                                   section=i + 1, sections=total)
                    await notify("Section finished",
                                 f"Next: {nxt} — Super+N to continue, Super+Alt+Space to stop.")
                    self._next_event.clear()
                    await self._next_event.wait()
            self.state.set(state="idle")
        except ModelsMissing as e:
            await notify("lector: models missing", str(e), urgency="critical")
            self.state.set(state="idle")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"read failed: {type(e).__name__}: {e}", flush=True)
            await notify("lector error", str(e), urgency="critical")
            self.state.set(state="idle")

    async def _play_section(self, idx: int, sec, announce_title: bool) -> None:
        chunks = chunk_text(sec.text)
        if announce_title and sec.title:
            chunks.insert(0, f"{sec.title}.")
        self.player.producer_done = False
        for j, chunk in enumerate(chunks):
            if self._skip or self._stopping:
                break
            wav = self.render_dir / f"{idx:03d}-{j:03d}.wav"
            await asyncio.to_thread(self.tts.synth, chunk, wav)
            if self._skip or self._stopping:
                break
            await self.player.enqueue(wav)
        self.player.producer_done = True
        await self.player.wait_done()

    # ------------------------------------------------------------- controls

    async def pause(self) -> dict:
        st = self.state.get().get("state")
        if st in ("playing", "paused") and self.player:
            await self.player.toggle_pause()
            paused = await self.player.is_paused()
            data = self.state.get()
            data["state"] = "paused" if paused else "playing"
            self.state.set(**data)
            return {"ok": True, "paused": paused}
        return {"ok": True, "noop": True}

    async def next_section(self) -> dict:
        st = self.state.get().get("state")
        if st == "section-wait":
            self._next_event.set()
            return {"ok": True}
        if st in ("playing", "paused") and self.player:
            self._skip = True
            await self.player.stop()  # drains wait_done, session moves on
            return {"ok": True, "skipped": True}
        return {"ok": True, "noop": True}

    async def stop_session(self) -> None:
        cur = asyncio.current_task()
        if self._fg_task and self._fg_task is not cur and not self._fg_task.done():
            self._fg_task.cancel()
            try:
                await self._fg_task
            except asyncio.CancelledError:
                pass
            self._fg_task = None
        if self.session and not self.session.done():
            self._stopping = True
            self._next_event.set()
            if self.player:
                await self.player.stop()
            self.session.cancel()
            try:
                await self.session
            except asyncio.CancelledError:
                pass
            self._stopping = False
        self.session = None
        self.state.set(state="idle")

    async def keep(self) -> dict:
        wavs = sorted(self.render_dir.glob("*.wav")) if self.render_dir.exists() else []
        if not wavs or not self.current_doc:
            await notify("Nothing to keep", "No rendered audio from a previous read.")
            return {"ok": True, "noop": True}
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
        out = self.cfg.audio_dir / f"{slugify(self.current_doc.title)}-{stamp}.wav"

        def _concat() -> None:
            import soundfile as sf

            with sf.SoundFile(str(wavs[0])) as first:
                sr, ch = first.samplerate, first.channels
            with sf.SoundFile(str(out), "w", samplerate=sr, channels=ch,
                              subtype="PCM_16") as sink:
                for w in wavs:
                    data, _ = sf.read(str(w))
                    sink.write(data)

        await asyncio.to_thread(_concat)
        await notify("Audio kept", str(out))
        return {"ok": True, "path": str(out)}

    # ------------------------------------------------------------- inbox

    async def on_inbox_file(self, path) -> None:
        # single-action dunst notification: middle-click reads (dunst's do_action default)
        choice = await ask("New document in inbox", f"{path.name} — middle-click to read",
                           [("read", "Read aloud")])
        if choice == "read":
            await self.start_read("file", str(path))


async def amain() -> None:
    cfg = C.load()
    cfg.ensure_dirs()
    daemon = Daemon(cfg)
    daemon.state.set(state="idle")
    server = await serve(C.CTL_SOCKET, daemon.handle)
    asyncio.ensure_future(asyncio.to_thread(daemon.tts.warmup))

    watcher = None
    if cfg.inbox_enabled:
        from .capture import InboxWatcher

        watcher = InboxWatcher(cfg.inbox_dir, asyncio.get_running_loop(),
                               daemon.on_inbox_file)
        watcher.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    if watcher:
        watcher.stop()
    await daemon.stop_session()
    if daemon.player:
        await daemon.player.close()
    server.close()
    daemon.state.set(state="idle")


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
