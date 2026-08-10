import asyncio
import contextlib
import datetime as dt
import os
import re
import shutil
import signal
import sys
import time
import uuid
from pathlib import Path

import numpy as np

from . import config as C
from . import dictation, notify as N
from .audio_in import (
    MAX_LEAD_IN_S, SILENCE_EPS, AudioUnavailable, MicStream, bluetooth_default,
    source_fingerprint,
)
from .brain import (
    CLEAN_SYSTEM, SCRIBE_REWRITE_SYSTEM, SCRIBE_WRITE_SYSTEM,
    Brain, BrainUnavailable,
)
from .capture import resolve_source, selection
from .ingest import Doc, load_source
from .inject import active_window, insert
from .ipc import serve
from .notify import ask, notify
from .playback import Player
from .state import StateFile
from .stt import SttUnavailable, build as build_stt
from .style import StyleBook
from .tts_kokoro import KokoroTTS, ModelsMissing, chunk_text

MIN_UTTERANCE_S = 0.35
# Ignore a second press this soon after a capture opens: that is a stutter, not an
# intentional stop.
PRESS_DEBOUNCE_S = 0.25


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:40] or "untitled"


class Daemon:
    def __init__(self, cfg: C.Config):
        self.cfg = cfg
        self.state = StateFile(C.STATE_FILE)
        self.tts = KokoroTTS(cfg.models_dir, cfg.voice, cfg.speed)
        self.brain = Brain(cfg)
        self.style = StyleBook(cfg)
        self.stt = build_stt(cfg)
        self.mic = MicStream(cfg.mic_device, cfg.mic_sample_rate,
                             cfg.mic_blocksize, cfg.mic_warmup_ms)
        self.player: Player | None = None
        self.session: asyncio.Task | None = None
        self.current_doc: Doc | None = None
        self.render_dir = C.RENDER_DIR / "current"
        self._next_event = asyncio.Event()
        self._skip = False
        self._stopping = False
        self._answers: dict[str, asyncio.Future] = {}
        self._fg_task: asyncio.Task | None = None

        # dictation state
        self._cap: dict | None = None          # active capture, if any
        self._latched = False
        self._pending: asyncio.Task | None = None   # deferred finalize (tap window)
        self._indicator: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None
        self._finalizing = False
        self._paused_by_dictation = False
        self._last_insertion = ""
        self._last_dictation: dict | None = None
        self._last_action = "read"
        self._interrupt = cfg.dictation_interrupt
        self._bt_warned = False
        self._mic_stale = False   # device moved mid-capture; rebind once idle

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
        if cmd in ("dictate", "scribe"):
            return await self.dictate_command(cmd, req)
        if cmd == "correct":
            asyncio.ensure_future(self.correct())
            return {"ok": True, "queued": True}
        if cmd == "pause":
            return await self.pause()
        if cmd == "stop":
            await self.cancel_capture()
            await self.stop_session()
            return {"ok": True}
        if cmd == "next":
            return await self.next_section()
        if cmd == "keep":
            return await self.keep()
        if cmd == "set":
            return self.set_option(req.get("key", ""), req.get("value", ""))
        if cmd == "status":
            return {"ok": True, **self.state.get(),
                    "dictation_interrupt": self._interrupt,
                    "listening": self._cap is not None, "latched": self._latched}
        if cmd == "answer":
            fut = self._answers.pop(req.get("id", ""), None)
            if fut and not fut.done():
                fut.set_result(req.get("choice") or None)
            return {"ok": True}
        if cmd == "ask":
            await notify("Not yet", "'ask' arrives in P4.")
            return {"ok": True, "pending_phase": True}
        return {"ok": False, "error": f"unknown command: {cmd}"}

    def set_option(self, key: str, value: str) -> dict:
        if key == "dictation_interrupt" and value in ("pause", "stop"):
            self._interrupt = value
            return {"ok": True, key: value}
        if key == "dictation_interrupt" and value == "toggle":
            self._interrupt = "stop" if self._interrupt == "pause" else "pause"
            return {"ok": True, key: self._interrupt}
        return {"ok": False, "error": f"cannot set {key!r} to {value!r}"}

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

    # ------------------------------------------------------------- dictation

    async def dictate_command(self, cmd: str, req: dict) -> dict:
        action = req.get("action", "toggle")
        purpose = "scribe" if cmd == "scribe" else "dictate"
        if action == "start":
            return await self.press(purpose, req)
        if action == "stop":
            return await self.release()
        # toggle: used when hold_mode is off, or from the menu
        if self._cap is not None:
            return await self.finish_capture()
        return await self.press(purpose, req, latched=True)

    async def press(self, purpose: str, req: dict, latched: bool = False) -> dict:
        # A press during the tap window means double-tap: latch instead of finalizing.
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
            self._pending = None
            self._latched = True
            await notify("Dictation latched", "Press again to finish.", timeout_ms=2000)
            return {"ok": True, "latched": True}
        if self._latched and self._cap is not None:
            return await self.finish_capture()
        if self._cap is not None:
            # A capture is already open, which means the key-release event never
            # arrived — Hyprland's `bindr` does not reliably fire when the modifier
            # is let go before the key. Finalize here so the chord degrades to
            # press-to-start / press-to-stop instead of listening until the
            # watchdog. The debounce keeps a stray double-press from killing a
            # capture that has only just begun.
            if time.monotonic() - self._cap["started"] < PRESS_DEBOUNCE_S:
                return {"ok": True, "noop": "just started"}
            print("press while capturing — no key release seen, finalizing",
                  flush=True)
            return await self.finish_capture()
        return await self.begin_capture(purpose, req, latched)

    async def release(self) -> dict:
        if self._cap is None or self._latched:
            return {"ok": True, "noop": True}
        held = self.mic.peek_seconds()
        if held >= self.cfg.latch_window_ms / 1000.0:
            return await self.finish_capture()   # a real hold: no added latency
        # A tap. Wait briefly to see if a second one arrives (double-tap = latch).
        self._pending = asyncio.ensure_future(self._deferred_finish())
        return {"ok": True, "deferred": True}

    async def _deferred_finish(self) -> None:
        try:
            await asyncio.sleep(self.cfg.latch_window_ms / 1000.0)
        except asyncio.CancelledError:
            return
        self._pending = None
        await self.finish_capture()

    # -------------------------------------------------------- device hot-plug

    async def rebind_mic(self, why: str) -> bool:
        """Rebind the capture stream to whatever the default source is now."""
        try:
            await asyncio.to_thread(self.mic.reopen)
        except AudioUnavailable as e:
            print(f"mic rebind failed ({why}): {e}", flush=True)
            return False
        self._mic_stale = False
        print(f"mic rebound to {self.mic.opened_for or 'default'} ({why})", flush=True)
        return True

    async def device_watch_loop(self) -> None:
        """Rebind the mic when PipeWire's default source moves.

        Bluetooth headsets disconnect constantly — AirPods do it whenever you put
        them down — and each reconnect creates a *new* PipeWire node under the same
        device name. A stream bound to the old node keeps running and returns
        silence, so without this the first dictation after a reconnect is empty and
        every one after it too, until the daemon restarts.
        """
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pactl", "subscribe",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL)
            except FileNotFoundError:
                print("pactl not found — mic hot-plug detection disabled", flush=True)
                return
            try:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    event = line.decode(errors="replace")
                    if "on source" not in event and "on server" not in event:
                        continue
                    # Let the reconnect settle: a single reconnect emits a burst,
                    # and the default source is briefly wrong mid-switch.
                    await asyncio.sleep(1.0)
                    if not self.mic.stale():
                        continue
                    if self._cap is not None:
                        self._mic_stale = True   # rebind after this capture
                        continue
                    await self.rebind_mic("default source changed")
            finally:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            await asyncio.sleep(2)   # pactl exited; resubscribe

    async def begin_capture(self, purpose: str, req: dict, latched: bool) -> dict:
        # Backstop for anything the watcher missed (it was busy, pactl died, the
        # device moved while a capture was running).
        if self._mic_stale or self.mic.stale():
            await self.rebind_mic("stale at capture start")
        if self.cfg.warn_bluetooth and not self._bt_warned:
            bt = await asyncio.to_thread(bluetooth_default)
            if bt:
                self._bt_warned = True
                await notify("Bluetooth mic in use",
                             f"{bt} — HFP mode degrades capture and playback. "
                             "Switch source for better accuracy.", timeout_ms=6000)
        try:
            await asyncio.to_thread(self.mic.start_collect)
        except AudioUnavailable as e:
            print(f"dictation failed: {e}", flush=True)
            await notify("lector: microphone unavailable", str(e), urgency="critical")
            return {"ok": False, "error": str(e)}

        sel = ""
        if purpose == "scribe":
            value = await selection()
            sel = value if isinstance(value, str) else ""
        win = await active_window()

        self._latched = latched
        self._cap = {"purpose": purpose, "started": time.monotonic(),
                     "clean": bool(req.get("clean", self.cfg.clean_by_default)),
                     "note": bool(req.get("note")),
                     "cloud": bool(req.get("cloud")),
                     "selection": sel, "app": win.get("class", "")}
        await self._interrupt_playback()
        self.state.set(state="listening")
        self._indicator = asyncio.ensure_future(self._indicator_loop())
        self._watchdog = asyncio.ensure_future(self._watchdog_loop())
        return {"ok": True, "listening": True}

    async def cancel_capture(self) -> None:
        if self._cap is None:
            return
        self._stop_helpers()
        await asyncio.to_thread(self.mic.stop_collect)
        self._cap = None
        self._latched = False
        await N.close()
        await self._resume_playback()
        self.state.set(state="idle")

    def _stop_helpers(self) -> None:
        for task in (self._indicator, self._watchdog, self._pending):
            if task is not None and not task.done():
                task.cancel()
        self._indicator = self._watchdog = self._pending = None

    async def _indicator_loop(self) -> None:
        try:
            while self._cap is not None:
                label = "Listening — latched" if self._latched else "Listening"
                secs = self.mic.peek_seconds()
                await N.progress(label, f"{secs:.0f}s", int(self.mic.level * 100))
                await asyncio.sleep(0.15)
        except asyncio.CancelledError:
            raise

    async def _watchdog_loop(self) -> None:
        """Backstop for a missed key-release event — without it, a dropped `bindr`
        would leave the mic collecting forever.

        A latched session has no release to miss, and latching exists precisely for
        long dictation, so it only stops at a much larger hard cap.
        """
        hard_cap = max(self.cfg.max_hold_s * 10, 1800.0)
        waited = 0.0
        try:
            while self._cap is not None:
                await asyncio.sleep(self.cfg.max_hold_s)
                waited += self.cfg.max_hold_s
                if self._cap is None:
                    return
                if not self._latched:
                    print(f"dictation watchdog fired after {waited:.0f}s "
                          "(no key release seen)", flush=True)
                    await self.finish_capture()
                    return
                if waited >= hard_cap:
                    print(f"latched dictation hit the {hard_cap:.0f}s cap", flush=True)
                    await notify("Dictation stopped", "Reached the maximum length.",
                                 timeout_ms=4000)
                    await self.finish_capture()
                    return
        except asyncio.CancelledError:
            return

    async def finish_capture(self) -> dict:
        if self._cap is None or self._finalizing:
            return {"ok": True, "noop": True}
        self._finalizing = True
        cap, self._cap = self._cap, None
        self._latched = False
        self._stop_helpers()
        try:
            audio = await asyncio.to_thread(self.mic.stop_collect)
            await N.close()
            secs = audio.size / self.cfg.mic_sample_rate if audio.size else 0.0
            wall = time.monotonic() - cap["started"]

            # The stream produced nothing but digital silence for the whole hold:
            # it is bound to a node that is gone or will not wake. Rebind so the
            # retry works, rather than failing silently on every future attempt.
            silent = (audio.size == 0 and wall >= MAX_LEAD_IN_S) or (
                audio.size > 0 and float(np.max(np.abs(audio))) < SILENCE_EPS)
            if silent:
                await self.rebind_mic("captured only digital silence")
                await self._resume_playback()
                await notify("Microphone reset",
                             "That capture was silent — the audio device had "
                             "changed. Try again.", timeout_ms=5000)
                self.state.set(state="idle")
                return {"ok": True, "empty": True, "rebound": True}

            if secs < MIN_UTTERANCE_S:
                await self._resume_playback()
                await notify("Nothing heard", "Hold the key while speaking.",
                             timeout_ms=2500)
                self.state.set(state="idle")
                return {"ok": True, "empty": True}

            self.state.set(state="processing")
            text = await asyncio.to_thread(
                self.stt.transcribe, audio, self.cfg.mic_sample_rate)
            text = (text or "").strip()
            if not text:
                await self._resume_playback()
                await notify("Nothing recognized", f"{secs:.1f}s of audio",
                             timeout_ms=2500)
                self.state.set(state="idle")
                return {"ok": True, "empty": True}

            text = self.style.apply_corrections(text)
            result = await self._post_process(cap, text)
            if result is None:
                self.state.set(state="idle")
                return {"ok": True, "cancelled": True}

            result = self.style.expand_shortcuts(result)
            method = await insert(result, self.cfg)
            self._last_insertion = result
            self._last_dictation = {"text": result, "mode": cap["purpose"],
                                    "app": cap["app"]}
            self._last_action = "dictate"

            log = await asyncio.to_thread(
                dictation.append_entry, self.cfg, result, cap["purpose"], cap["app"])
            promoted = None
            if cap["note"]:
                promoted = await asyncio.to_thread(
                    dictation.promote, self.cfg, result, cap["purpose"], cap["app"])
            # Only report a lane when the brain actually ran; brain.last_lane is
            # sticky, so a raw transcript would otherwise inherit the previous
            # call's label and make the log lie.
            used_llm = cap["purpose"] == "scribe" or cap["clean"]
            lane = f", lane={self.brain.last_lane}" if used_llm else ""
            print(f"{cap['purpose']}: {secs:.1f}s -> {len(result.split())} words "
                  f"({method}{lane})", flush=True)
            if method == "held":
                await notify("Keys still held",
                             "Text is on the clipboard — typing it while a modifier "
                             "is down would fire your shortcuts instead.",
                             timeout_ms=6000)
            elif method == "failed":
                await notify("Insertion failed", "Text is on the clipboard.",
                             urgency="critical")
            elif promoted:
                await notify("Dictation saved as a note", str(promoted), timeout_ms=4000)
            return {"ok": True, "words": len(result.split()), "method": method,
                    "log": str(log)}
        except (SttUnavailable, BrainUnavailable) as e:
            print(f"dictation failed: {e}", flush=True)
            await notify("lector: dictation unavailable", str(e), urgency="critical")
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            print(f"dictation failed: {type(e).__name__}: {e}", flush=True)
            await notify("lector error", str(e), urgency="critical")
            return {"ok": False, "error": str(e)}
        finally:
            self._finalizing = False
            await self._resume_playback()
            if self._mic_stale:
                # The device moved while this capture was running; the watcher
                # deferred to avoid cutting it short.
                await self.rebind_mic("deferred from mid-capture")
            if self.state.get().get("state") in ("listening", "processing"):
                self.state.set(state="idle")

    async def _post_process(self, cap: dict, text: str) -> str | None:
        """Raw transcript -> what actually gets inserted."""
        purpose, cloud = cap["purpose"], cap["cloud"]
        if purpose == "scribe":
            rewriting = bool(cap["selection"])
            mode = "scribe_rewrite" if rewriting else "scribe"
            base = SCRIBE_REWRITE_SYSTEM if rewriting else SCRIBE_WRITE_SYSTEM
            system = self.style.system_prompt(base, cap["app"])
            return await asyncio.to_thread(
                self.brain.run_voice, mode, cap["selection"] or text,
                text if rewriting else "", cloud, system)
        if cap["clean"]:
            system = self.style.system_prompt(CLEAN_SYSTEM, cap["app"])
            return await asyncio.to_thread(
                self.brain.run_voice, "clean", text, "", cloud, system)
        return text

    async def correct(self) -> None:
        """Learn from a fix: select the corrected text, then hit the bind (D51)."""
        if not self._last_insertion:
            await notify("Nothing to correct", "No recent insertion to compare against.")
            return
        value = await selection()
        after = value if isinstance(value, str) else ""
        if not after.strip():
            await notify("Nothing selected",
                         "Select your corrected text, then press the bind again.")
            return
        learned = await asyncio.to_thread(
            self.style.learn, self._last_insertion, after)
        n = learned["corrections"]
        await notify("Correction learned",
                     f"{n} vocabulary fix{'es' if n != 1 else ''} + 1 style example",
                     timeout_ms=4000)
        print(f"correction learned: {n} vocab entries", flush=True)

    # ------------------------------------------------------------- playback glue

    async def _interrupt_playback(self) -> None:
        st = self.state.get().get("state")
        if st != "playing":
            return
        if self._interrupt == "stop":
            await self.stop_session()
        else:
            await self.pause()
            self._paused_by_dictation = True

    async def _resume_playback(self) -> None:
        if not self._paused_by_dictation:
            return
        self._paused_by_dictation = False
        if not self.player or not self.session or self.session.done():
            return
        # Ask the player, not the state file: the state was overwritten with
        # "listening"/"processing" while the dictation ran.
        if await self.player.is_paused():
            await self.player.toggle_pause()
        data = self.state.get()
        data["state"] = "playing"
        self.state.set(**data)

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
            self._last_action = "read"
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
            self._last_action = "read"
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
        model = (self.cfg.llm_cloud_model if self.brain.last_lane == "cloud"
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
            await self.player.stop()
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
        """Context-sensitive (D41): after a dictation this promotes the transcript,
        after a read it keeps the rendered audio."""
        if self._last_action == "dictate" and self._last_dictation:
            d = self._last_dictation
            out = await asyncio.to_thread(
                dictation.promote, self.cfg, d["text"], d["mode"], d["app"])
            await notify("Dictation saved as a note", str(out))
            return {"ok": True, "path": str(out), "kind": "dictation"}

        wavs = sorted(self.render_dir.glob("*.wav")) if self.render_dir.exists() else []
        if not wavs or not self.current_doc:
            await notify("Nothing to keep", "No rendered audio or recent dictation.")
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
        return {"ok": True, "path": str(out), "kind": "audio"}

    # ------------------------------------------------------------- inbox

    async def on_inbox_file(self, path) -> None:
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

    async def warm_stt() -> None:
        try:
            await asyncio.to_thread(daemon.stt.load)
            print("stt model ready", flush=True)
            # Long captures segment with Silero; build it now so finalizing a long
            # dictation never blocks on a download.
            await asyncio.to_thread(daemon.stt.load_vad)
            print("stt vad ready", flush=True)
        except SttUnavailable as e:
            print(f"stt unavailable: {e}", flush=True)
        except Exception as e:  # noqa: BLE001 — VAD is optional; short captures work
            print(f"stt vad unavailable ({type(e).__name__}: {e}) — "
                  "captures over the segment limit may be truncated", flush=True)

    if daemon.stt.available():
        asyncio.ensure_future(warm_stt())
    else:
        print("stt model not downloaded yet — first dictation will fetch it",
              flush=True)

    watcher_task = asyncio.ensure_future(daemon.device_watch_loop())
    print(f"watching audio devices (default source: "
          f"{source_fingerprint() or 'none'})", flush=True)

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

    watcher_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watcher_task
    if watcher:
        watcher.stop()
    await daemon.cancel_capture()
    await daemon.stop_session()
    daemon.mic.close()
    if daemon.player:
        await daemon.player.close()
    server.close()
    daemon.state.set(state="idle")


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
