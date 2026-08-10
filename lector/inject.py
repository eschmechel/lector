"""Put text into whatever window has focus.

Typing, not pasting. Synthesizing a paste chord looked faster on paper, but on this
stack it is both broken and harmful:

  * `wtype -M ctrl -M shift -k v -m shift -m ctrl` does not paste. Measured against
    a terminal reading one line: the clipboard canary never arrived, while wtype
    still exited 0 — so the daemon reported a successful "paste" and inserted
    nothing.
  * Worse, two `-M` modifiers get stranded. wtype's man page says modifiers are
    released when the program terminates, but the release loses a race with the
    virtual keyboard being destroyed, so Ctrl+Shift stay logically held. The next
    Escape then reads as Ctrl+Shift+Escape, which on HyDE launches the system
    monitor. One `-M` does not leak; two do.

Typing has neither problem and was exact over 137 characters. It costs about
9ms/character, which is what the user's previous dictation tool did anyway.
A chord can still be configured per app for anyone whose stack handles it, but a
chord with two or more modifiers is refused rather than silently stranding them.
"""

import asyncio
import json

MOD_ALIASES = {"super": "logo", "meta": "logo", "control": "ctrl"}
TYPE = "type"


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


def method_for(window_class: str, cfg) -> str:
    """"type", or a chord like "ctrl+v", for the focused window."""
    if window_class:
        if window_class in cfg.inject_chords:
            return cfg.inject_chords[window_class]
        low = window_class.lower()
        for known, method in cfg.inject_chords.items():
            if known.lower() in low:
                return method
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


def chord_is_safe(chord: str) -> bool:
    """Two or more modifiers get stranded by wtype; refuse those."""
    return len([p for p in chord.split("+") if p.strip()]) <= 2


async def get_clipboard() -> str | None:
    rc, out = await _run("wl-paste", "-n", "-t", "text")
    return out if rc == 0 else None


async def set_clipboard(text: str) -> None:
    await _run("wl-copy", stdin=text.encode())


async def type_text(text: str, cfg) -> bool:
    # wtype rejects -d 0; 1ms is the floor and the per-keystroke cost dominates.
    delay = max(1, cfg.inject_type_delay_ms)
    rc, _ = await _run("wtype", "-d", str(delay), "--", text)
    return rc == 0


async def paste_text(text: str, chord: str, cfg) -> bool:
    previous = await get_clipboard() if cfg.inject_restore_clipboard else None
    await set_clipboard(text)
    # wl-copy forks a server to own the selection; let the compositor publish the
    # new offer before telling the target to paste.
    await asyncio.sleep(0.06)
    args = _chord_args(chord)
    if not args:
        return False
    rc, _ = await _run("wtype", *args)
    if rc == 0 and cfg.inject_restore_clipboard:
        await asyncio.sleep(0.15)
        if previous:
            await set_clipboard(previous)
    return rc == 0


async def insert(text: str, cfg) -> str:
    """Insert text at the cursor. Returns the method actually used."""
    if not text:
        return "noop"

    win = await active_window()
    method = method_for(win.get("class", ""), cfg)

    if method != TYPE and not chord_is_safe(method):
        print(f"inject: refusing chord {method!r} — two or more modifiers strand "
              "themselves in the compositor; typing instead", flush=True)
        method = TYPE

    # Keep the text on the clipboard regardless: if injection lands in the wrong
    # window or not at all, it is still recoverable with a normal paste.
    if cfg.inject_copy_to_clipboard and method == TYPE:
        await set_clipboard(text)

    if method == TYPE:
        return "type" if await type_text(text, cfg) else "failed"
    if await paste_text(text, method, cfg):
        return "paste"
    return "type" if await type_text(text, cfg) else "failed"
