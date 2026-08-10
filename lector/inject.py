"""Put text into whatever window has focus.

Typing character-by-character with wtype is reliable but slow — at 3ms/char a
200-word dictation spends ~3.6s typing itself out. So the default path puts the text
on the clipboard and synthesizes a paste, which is instant regardless of length. The
paste chord is per-app because terminals want ctrl+shift+v.
"""

import asyncio
import json

MOD_ALIASES = {"super": "logo", "meta": "logo", "control": "ctrl"}


async def _run(*cmd: str, stdin: bytes | None = None) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except FileNotFoundError:
        return 127, ""
    out, _ = await proc.communicate(stdin)
    return proc.returncode, out.decode(errors="replace")


async def active_window() -> dict:
    rc, out = await _run("hyprctl", "activewindow", "-j")
    if rc != 0 or not out.strip():
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}


def chord_for(window_class: str, cfg) -> str:
    """Exact class match first, then a case-insensitive contains match."""
    if not window_class:
        return cfg.inject_default_chord
    if window_class in cfg.inject_chords:
        return cfg.inject_chords[window_class]
    low = window_class.lower()
    for known, chord in cfg.inject_chords.items():
        if known.lower() in low:
            return chord
    return cfg.inject_default_chord


def _chord_args(chord: str) -> list[str]:
    parts = [p.strip().lower() for p in chord.split("+") if p.strip()]
    if not parts:
        return []
    *mods, key = parts
    mods = [MOD_ALIASES.get(m, m) for m in mods]
    args: list[str] = []
    for m in mods:
        args += ["-M", m]
    args += ["-k", key]
    for m in reversed(mods):
        args += ["-m", m]
    return args


async def get_clipboard() -> str | None:
    rc, out = await _run("wl-paste", "-n", "-t", "text")
    return out if rc == 0 else None


async def set_clipboard(text: str) -> None:
    await _run("wl-copy", stdin=text.encode())


async def clear_clipboard() -> None:
    await _run("wl-copy", "--clear")


async def type_text(text: str, cfg) -> bool:
    rc, _ = await _run("wtype", "-d", str(cfg.inject_type_delay_ms), "--", text)
    return rc == 0


async def insert(text: str, cfg) -> str:
    """Insert text at the cursor. Returns the method actually used."""
    if not text:
        return "noop"

    win = await active_window()
    chord = chord_for(win.get("class", ""), cfg)

    previous = await get_clipboard() if cfg.inject_restore_clipboard else None
    await set_clipboard(text)
    # wl-copy forks a server to own the selection; give the compositor a moment to
    # publish the new offer before the target app is told to paste.
    await asyncio.sleep(0.06)

    args = _chord_args(chord)
    rc, _ = await _run("wtype", *args) if args else (1, "")

    if rc != 0:
        if cfg.inject_fallback_type and await type_text(text, cfg):
            if previous is not None:
                await set_clipboard(previous)
            return "type"
        return "failed"

    if cfg.inject_restore_clipboard:
        # Let the paste land before taking the clipboard back.
        await asyncio.sleep(0.15)
        if previous:
            await set_clipboard(previous)
        elif previous == "":
            await clear_clipboard()
    return "paste"
