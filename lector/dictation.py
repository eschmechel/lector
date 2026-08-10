"""Where dictated text goes after it has been inserted.

Default is a rolling daily log — one file per day rather than one per utterance,
because once dictation is the main input method a note per utterance is dozens of
files a day. Any single entry can be promoted to its own note.
"""

import datetime as dt
import re
from pathlib import Path


def _slug(text: str, words: int = 6) -> str:
    head = " ".join(text.split()[:words]).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", head).strip("-")
    return slug[:48] or "dictation"


def log_path(cfg, when: dt.datetime | None = None) -> Path:
    when = when or dt.datetime.now()
    return cfg.dictation_dir / f"{when:%Y-%m-%d}.md"


def append_entry(cfg, text: str, mode: str = "raw", app: str = "") -> Path:
    """Append one utterance to today's log. Returns the log path."""
    when = dt.datetime.now()
    path = log_path(cfg, when)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"## {when:%H:%M:%S} · {mode}" + (f" · {app}" if app else "")
    fresh = not path.exists()
    with path.open("a", encoding="utf-8") as fh:
        if fresh:
            fh.write(f"---\ndate: {when:%Y-%m-%d}\nkind: dictation-log\n---\n\n")
        fh.write(f"{header}\n\n{text.strip()}\n\n")
    return path


def promote(cfg, text: str, mode: str = "raw", app: str = "") -> Path:
    """Save one dictation as its own note (D41)."""
    when = dt.datetime.now()
    out = cfg.notes_out_dir / f"{_slug(text)}-dictation-{when:%Y%m%d-%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"---\nkind: dictation\nmode: {mode}\n"
        + (f"app: {app}\n" if app else "")
        + f"date: {when:%Y-%m-%d %H:%M}\n---\n\n{text.strip()}\n",
        encoding="utf-8")
    return out
