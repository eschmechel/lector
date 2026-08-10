"""Daemon-independent sanity checks, used by smoke.sh. Exit 0 iff nothing failed."""

import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASS, SKIP, FAIL = [], [], []


def check(name: str, fn) -> None:
    try:
        result = fn()
        (SKIP if result == "skip" else PASS).append(name)
        print(f"  {'skip' if result == 'skip' else 'ok  '}  {name}")
    except Exception as e:  # noqa: BLE001
        FAIL.append(name)
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def _tmp_cfg(td: str):
    from . import config

    return config.Config(notes_dir=Path(td), models_dir=Path(td))


def binaries():
    missing = [b for b in ("mpv", "wl-paste", "wl-copy", "dunstify", "fzf", "wtype")
               if not shutil.which(b)]
    assert not missing, f"missing binaries: {missing}"


def config_loads():
    from . import config

    cfg = config.load()
    assert cfg.long_doc_words > 0
    assert cfg.mic_blocksize == cfg.mic_sample_rate * cfg.mic_block_ms // 1000
    assert cfg.dictation_interrupt in ("pause", "stop")
    assert cfg.lane_for("scribe") in ("local", "cloud")
    assert cfg.lane_for("nonexistent-mode") == cfg.llm_provider


def ingest_fixture():
    from .ingest import load_source

    doc = load_source(REPO / "tests" / "fixtures" / "sample.md")
    assert doc.title == "Sample Document", doc.title
    assert len(doc.sections) == 3, [s.title for s in doc.sections]
    body = " ".join(s.text for s in doc.sections)
    assert "[code block omitted]" in body
    assert "[table omitted]" in body
    assert "print(" not in body
    assert "example.com" not in body


def chunker():
    from .tts_kokoro import chunk_text

    chunks = chunk_text("One. " * 500, limit=450)
    assert chunks and all(len(c) <= 450 for c in chunks)
    assert chunk_text("") == []
    assert len(chunk_text("word " * 200)[0]) <= 140, "first chunk must stay small"
    glyphy = chunk_text("\U000f08c7  ~ ❯ hello world")
    assert glyphy == ["~ hello world"], f"glyphs must be stripped, got {glyphy!r}"


def speech_normalizer():
    from .tts_kokoro import chunk_text

    out = chunk_text("-h, --help            show this help message and exit")[0]
    assert "dash h" in out and "dash dash help" in out, out
    out = chunk_text("choose from read,summarize,annotate")[0]
    assert "read, summarize, annotate" in out, out
    out = chunk_text("it costs 1,000 dollars at -5 degrees in a well-known town")[0]
    assert "1,000" in out and "minus 5" in out and "well-known" in out, out
    out = chunk_text("see https://open.spotify.com/show/0sxpFsg?si=f5a7 for details")[0]
    assert out == "see link to open.spotify.com for details", out


def uri_decoding():
    from .capture import as_doc_path

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "has space.md"
        p.write_text("x")
        uri = f"file://{str(p).replace(' ', '%20')}"
        assert as_doc_path(uri) == p, "percent-encoded file URI must resolve"
        assert as_doc_path(f"file://{p}\r") == p, "CRLF from uri-list must be tolerated"
    long_sentence = "a" * 300 + ".md"  # >255-char path component: OSError, not a crash
    assert as_doc_path(long_sentence) is None


def brain_offline():
    from .brain import _PROMPTS, _WORD_CAPS, strip_thinking

    expected = {"summarize", "annotate", "smart", "clean", "scribe", "scribe_rewrite"}
    assert set(_PROMPTS) == expected == set(_WORD_CAPS)
    assert strip_thinking("<think>internal</think>real output") == "real output"
    assert strip_thinking("<think>unterminated...") == ""


def inject_methods():
    from .inject import _chord_args, chord_is_safe, method_for

    with tempfile.TemporaryDirectory() as td:
        cfg = _tmp_cfg(td)
        assert method_for("kitty", cfg) == "type", "typing is the default everywhere"
        assert method_for("", cfg) == "type"
        cfg.inject_chords = {"firefox": "ctrl+v"}
        assert method_for("firefox", cfg) == "ctrl+v"
        assert method_for("org.mozilla.firefox", cfg) == "ctrl+v", "contains-match"
        assert method_for("kitty", cfg) == "type", "unlisted apps keep the default"

    # Measured: one -M modifier is released cleanly, two get stranded in the
    # compositor and turn the user's next Escape into ctrl+shift+Escape.
    assert chord_is_safe("ctrl+v")
    assert chord_is_safe("v")
    assert not chord_is_safe("ctrl+shift+v")
    assert _chord_args("ctrl+v") == ["-M", "ctrl", "-k", "v", "-m", "ctrl"]


def modifier_watch():
    """Physically-held modifiers must be readable, so injection can wait them out."""
    from .modkeys import MOD_CODES, ModifierWatcher

    watcher = ModifierWatcher()
    if not watcher.available():
        return "skip"          # not a member of the `input` group
    try:
        held = watcher.held()
        assert isinstance(held, list), type(held)
        # Only real modifier codes are ever reported, and the ioctl must not raise
        # on any of the machine's input devices.
        assert all(code in MOD_CODES for code in held), held
        watcher.held()         # a second read must also succeed
    finally:
        watcher.close()


def style_book():
    from .style import StyleBook

    with tempfile.TemporaryDirectory() as td:
        cfg = _tmp_cfg(td)
        cfg.style_card = Path(td) / "style.md"
        cfg.style_card.write_text("Short sentences. No exclamation marks.")
        cfg.shortcuts = {"my calendar link": "https://cal.example/x"}
        cfg.style_profiles = {"kitty": "Terse."}
        cfg.vocabulary = ["Hyprland"]
        sb = StyleBook(cfg)
        assert sb.expand_shortcuts("send My Calendar Link please") == \
            "send https://cal.example/x please", "shortcuts are case-insensitive"
        assert sb.expand_shortcuts("no match here") == "no match here"
        prompt = sb.system_prompt("BASE PROMPT", "kitty")
        for fragment in ("BASE PROMPT", "Short sentences", "Terse.", "Hyprland"):
            assert fragment in prompt, f"{fragment!r} missing from system prompt"


def style_learning():
    from .style import StyleBook

    with tempfile.TemporaryDirectory() as td:
        cfg = _tmp_cfg(td)
        cfg.style_card = Path(td) / "style.md"
        sb = StyleBook(cfg)
        got = sb.learn("deploy to hyper land cluster", "deploy to Hyprland cluster")
        assert got["corrections"] >= 1, got
        assert sb.apply_corrections("the hyper land box") == "the Hyprland box"
        # Learned data must survive a reload — it is a file, not memory.
        assert StyleBook(cfg).apply_corrections("hyper land") == "Hyprland"
        # A wholesale rewrite is a style example, not a vocabulary entry.
        before = {**sb.corrections}
        sb.learn("a", "completely different sentence with many new words here")
        assert sb.corrections == before, "long replacements must not become vocab"


def dictation_log():
    from . import dictation

    with tempfile.TemporaryDirectory() as td:
        cfg = _tmp_cfg(td)
        p1 = dictation.append_entry(cfg, "first line", "raw", "kitty")
        p2 = dictation.append_entry(cfg, "second line", "clean", "firefox")
        assert p1 == p2, "same day should append to one file"
        body = p1.read_text()
        assert "first line" in body and "second line" in body
        assert body.count("kind: dictation-log") == 1, "frontmatter written once"
        note = dictation.promote(cfg, "promoted text here", "raw", "kitty")
        assert note.exists() and "promoted text here" in note.read_text()
        assert note.parent == cfg.notes_out_dir


def press_release_machine():
    """A press while already capturing must finalize.

    Hyprland's `bindr` does not fire reliably when the modifier is released before
    the key, so without this the mic stays open until the watchdog and the chord
    feels stuck. Regression test for that.
    """
    import asyncio

    from .daemon import PRESS_DEBOUNCE_S, Daemon

    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = _tmp_cfg(td)
            cfg.style_card = Path(td) / "style.md"
            d = Daemon(cfg)
            finished: list[str] = []

            async def fake_begin(purpose, req, latched):
                d._cap = {"purpose": purpose, "started": time.monotonic(),
                          "clean": False, "note": False, "cloud": False,
                          "selection": "", "app": ""}
                d._latched = latched
                return {"ok": True}

            async def fake_finish():
                finished.append(d._cap["purpose"] if d._cap else "?")
                d._cap = None
                d._latched = False
                return {"ok": True}

            d.begin_capture = fake_begin
            d.finish_capture = fake_finish

            await d.press("dictate", {})
            assert d._cap is not None, "first press must open a capture"

            # Immediately again: a stutter, not a stop.
            await d.press("dictate", {})
            assert d._cap is not None and not finished, "debounce must hold"

            # Past the debounce, a press means the release was missed: finalize.
            d._cap["started"] -= PRESS_DEBOUNCE_S + 0.1
            await d.press("dictate", {})
            assert finished == ["dictate"], f"press must finalize, got {finished}"
            assert d._cap is None

            # Normal path: a held release still finalizes directly.
            await d.press("dictate", {})
            d.mic.peek_seconds = lambda: 5.0
            await d.release()
            assert finished == ["dictate", "dictate"], finished

    asyncio.run(scenario())


def source_identity():
    """The default source must be identified by node id, not just name.

    A Bluetooth headset keeps its device name across a reconnect but comes back as
    a new PipeWire node, so a name-only check would never notice it moved.
    """
    from .audio_in import MicStream, source_fingerprint

    fp = source_fingerprint()
    if not fp:
        return "skip"          # no pulse/pipewire on this box
    assert "#" in fp, f"fingerprint must carry the node id, got {fp!r}"

    mic = MicStream("pipewire", 16000, 1600, 300)
    assert mic.stale() is False, "a stream that was never opened cannot be stale"
    try:
        mic._stream = object()     # pretend it is open (never a real device here)...
        mic.opened_for = "bluez_input.aa:bb#999"   # ...against a node that is gone
        assert mic.stale() is True, "a moved default source must read as stale"
        mic.opened_for = fp
        assert mic.stale() is False, "unchanged source must not read as stale"
    finally:
        mic._stream = None


def mic_capture():
    from . import config
    from .audio_in import AudioUnavailable, MicStream

    cfg = config.load()
    mic = MicStream(cfg.mic_device, cfg.mic_sample_rate, cfg.mic_blocksize,
                    cfg.mic_warmup_ms)
    try:
        mic.open()
    except AudioUnavailable:
        return "skip"
    try:
        mic.start_collect()
        time.sleep(0.6)
        audio = mic.stop_collect()
        # Zero frames is legitimate: a SUSPENDED node emits exact zeros for a
        # while after opening and that lead-in is deliberately dropped. What must
        # hold is that the stream opened and produced correctly-typed data if any.
        assert mic.opened_for, "an open stream must record which source it bound to"
        if audio.size:
            assert audio.dtype.name == "float32", audio.dtype
    finally:
        mic.close()


def lead_in_drop():
    """Digital silence before the first real audio is dropped, signal is kept.

    Deterministic: drives the callback directly instead of depending on whether
    this machine's mic happens to be awake.
    """
    import numpy as np

    from .audio_in import SILENCE_EPS, MicStream

    mic = MicStream("pipewire", 16000, 1600, warmup_ms=0)
    mic._opened_at = time.monotonic() - 10   # past the fixed warm-up
    # Arm collection by hand: start_collect() would open real audio hardware, and
    # a PortAudio stream left dangling next to a re-init segfaults the process.
    mic._collecting = True
    mic._saw_signal = False
    mic._collect_started = time.monotonic()

    silence = np.zeros((1600, 1), dtype=np.float32)
    speech = np.full((1600, 1), 0.05, dtype=np.float32)

    for _ in range(3):
        mic._on_block(silence, 1600, None, None)
    assert mic.peek_seconds() == 0.0, "silent lead-in must be dropped"

    mic._on_block(speech, 1600, None, None)
    mic._on_block(silence, 1600, None, None)   # a pause mid-sentence is kept
    audio = mic.stop_collect()
    assert audio.size == 3200, f"expected speech + trailing pause, got {audio.size}"
    assert float(np.max(np.abs(audio))) > SILENCE_EPS


def llm_roundtrip():
    import httpx

    from . import config
    from .brain import Brain

    cfg = config.load()
    try:
        httpx.get(f"{cfg.llm_local_base_url}/api/version", timeout=2)
    except httpx.ConnectError:
        return "skip"
    out = Brain(cfg).complete("Answer with exactly one word.", "What is 2+2?",
                              lane="local")
    assert out.strip(), "empty LLM reply"


def tts_render():
    from . import config
    from .tts_kokoro import KokoroTTS

    cfg = config.load()
    tts = KokoroTTS(cfg.models_dir, cfg.voice, cfg.speed)
    if not tts.models_present():
        return "skip"
    with tempfile.TemporaryDirectory() as td:
        out = tts.synth("Lector smoke test.", Path(td) / "t.wav")
        assert out.stat().st_size > 10_000, "suspiciously small render"


def stt_roundtrip():
    """Speak a sentence with Kokoro, hear it back with Parakeet."""
    import numpy as np
    import soundfile as sf

    from . import config
    from .stt import build
    from .tts_kokoro import KokoroTTS

    cfg = config.load()
    tts = KokoroTTS(cfg.models_dir, cfg.voice, cfg.speed)
    stt = build(cfg)
    if not tts.models_present() or not stt.available():
        return "skip"
    sentence = "The quick brown fox jumps over the lazy dog."
    with tempfile.TemporaryDirectory() as td:
        wav = tts.synth(sentence, Path(td) / "t.wav")
        data, sr = sf.read(str(wav), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        n = int(round(data.size * 16000 / sr))
        data = np.interp(np.linspace(0, data.size - 1, n),
                         np.arange(data.size), data).astype(np.float32)
    heard = stt.transcribe(data, 16000).lower()
    assert "quick brown fox" in heard, f"heard {heard!r}"


def main() -> None:
    print("lector selftest:")
    check("required binaries", binaries)
    check("config loads", config_loads)
    check("ingest sample.md", ingest_fixture)
    check("text chunker", chunker)
    check("speech normalizer", speech_normalizer)
    check("file-URI decoding", uri_decoding)
    check("brain prompts/think-strip", brain_offline)
    check("injection method selection", inject_methods)
    check("held-modifier detection", modifier_watch)
    check("style card + shortcuts", style_book)
    check("correction learning", style_learning)
    check("dictation log + promote", dictation_log)
    check("press/release state machine", press_release_machine)
    check("audio source identity", source_identity)
    check("silent lead-in dropped", lead_in_drop)
    check("mic capture", mic_capture)
    check("local LLM roundtrip", llm_roundtrip)
    check("kokoro TTS render", tts_render)
    check("TTS -> STT roundtrip", stt_roundtrip)
    total = len(PASS) + len(SKIP) + len(FAIL)
    print(f"selftest: {len(PASS)} passed, {len(SKIP)} skipped, {len(FAIL)} failed (of {total})")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
