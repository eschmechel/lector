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


def inject_chords():
    from .inject import _chord_args, chord_for

    with tempfile.TemporaryDirectory() as td:
        cfg = _tmp_cfg(td)
        assert chord_for("kitty", cfg) == "ctrl+shift+v"
        assert chord_for("org.kde.kitty", cfg) == "ctrl+shift+v", "contains-match"
        assert chord_for("firefox", cfg) == "ctrl+v"
        assert chord_for("", cfg) == "ctrl+v"
    assert _chord_args("ctrl+shift+v") == [
        "-M", "ctrl", "-M", "shift", "-k", "v", "-m", "shift", "-m", "ctrl"]
    assert _chord_args("ctrl+v") == ["-M", "ctrl", "-k", "v", "-m", "ctrl"]


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
        assert audio.size > 0, "stream opened but captured no frames"
        assert audio.dtype.name == "float32"
    finally:
        mic.close()


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
    check("paste chord selection", inject_chords)
    check("style card + shortcuts", style_book)
    check("correction learning", style_learning)
    check("dictation log + promote", dictation_log)
    check("mic capture", mic_capture)
    check("local LLM roundtrip", llm_roundtrip)
    check("kokoro TTS render", tts_render)
    check("TTS -> STT roundtrip", stt_roundtrip)
    total = len(PASS) + len(SKIP) + len(FAIL)
    print(f"selftest: {len(PASS)} passed, {len(SKIP)} skipped, {len(FAIL)} failed (of {total})")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
