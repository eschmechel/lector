"""Daemon-independent sanity checks, used by smoke.sh. Exit 0 iff nothing failed."""

import shutil
import sys
import tempfile
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


def binaries():
    missing = [b for b in ("mpv", "wl-paste", "dunstify", "rofi", "zenity") if not shutil.which(b)]
    assert not missing, f"missing binaries: {missing}"


def config_loads():
    from . import config

    cfg = config.load()
    assert cfg.long_doc_words > 0


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


def main() -> None:
    print("lector selftest:")
    check("required binaries", binaries)
    check("config loads", config_loads)
    check("ingest sample.md", ingest_fixture)
    check("text chunker", chunker)
    check("kokoro TTS render", tts_render)
    total = len(PASS) + len(SKIP) + len(FAIL)
    print(f"selftest: {len(PASS)} passed, {len(SKIP)} skipped, {len(FAIL)} failed (of {total})")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
