import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as C
from .ipc import request

MENU = [
    ("Read clipboard/selection", "read"),
    ("Read file…", "pick"),
    ("Summarize → read  (P2)", "summarize"),
    ("Annotate  (P2)", "annotate"),
    ("Pause / Resume", "pause"),
    ("Next section", "next"),
    ("Keep last audio", "keep"),
    ("Stop", "stop"),
]

def send(cmd: str, **args) -> dict:
    try:
        return request(C.CTL_SOCKET, cmd, **args)
    except (ConnectionRefusedError, FileNotFoundError):
        print("lectord is not running (systemctl --user start lector)", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------- tui / float

def in_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def spawn_floating(*ctl_args: str) -> None:
    """Re-run this lectorctl command inside a floating terminal (lector-menu class)."""
    ctl = str(Path(sys.argv[0]).resolve())
    term = os.environ.get("TERMINAL") or "kitty"
    name = Path(term).name
    if name in ("kitty", "foot", "ghostty"):
        clsflag = "--app-id=lector-menu" if name == "foot" else "--class=lector-menu"
        argv = [term, clsflag, ctl, *ctl_args]
    else:
        argv = [term, "--class", "lector-menu", "-e", ctl, *ctl_args]
    subprocess.Popen(argv, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def fzf_choose(options: list[str], prompt: str) -> str | None:
    try:
        res = subprocess.run(
            ["fzf", "--reverse", "--no-info", f"--prompt={prompt}> "],
            input="\n".join(options), capture_output=True, text=True,
        )
    except FileNotFoundError:
        print("fzf not found", file=sys.stderr)
        sys.exit(1)
    choice = res.stdout.strip()
    return choice or None


def find_docs() -> list[str]:
    cfg = C.load()
    dirs = [str(d) for d in cfg.picker_dirs if d.is_dir()]
    if not dirs:
        return []
    if shutil.which("fd"):
        # -L: follow symlinks (type check applies to the target); --no-ignore: include
        # gitignored files. Hidden dot-dirs stay excluded (fd default).
        cmd = ["fd", "-a", "-L", "--no-ignore", "-t", "f",
               "-e", "md", "-e", "txt", "-e", "pdf"]
        for ex in cfg.picker_exclude:
            cmd += ["-E", ex]
        cmd += [".", *dirs]
    else:
        cmd = ["find", "-L", *dirs, "-name", ".*", "-prune", "-o", "-type", "f",
               "(", "-name", "*.md", "-o", "-name", "*.txt", "-o", "-name", "*.pdf", ")"]
        for ex in cfg.picker_exclude:
            cmd += ["-not", "-path", f"*/{ex}/*"]
        cmd += ["-print"]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout.splitlines()

    seen: set[str] = set()
    unique: list[tuple[float, str]] = []
    for p in out:
        try:
            path = Path(p)
            real = str(path.resolve())
            if real in seen:
                continue
            seen.add(real)
            unique.append((path.stat().st_mtime, p))
        except OSError:
            continue
    unique.sort(reverse=True)
    return [p for _, p in unique[: cfg.picker_limit]]


def pick_file_tui() -> str | None:
    files = find_docs()
    if not files:
        print("no documents found under configured picker dirs", file=sys.stderr)
        return None
    home = str(Path.home())
    shown = [f.replace(home, "~", 1) for f in files]
    choice = fzf_choose(shown, "read file")
    return str(Path(choice.replace("~", home, 1))) if choice else None


def run_menu() -> None:
    if not in_tty():
        spawn_floating("menu")
        return
    choice = fzf_choose([label for label, _ in MENU], "lector")
    if not choice:
        return
    action = dict(MENU)[choice]
    if action == "pick":
        path = pick_file_tui()
        if path:
            send("read", source="file", path=path)
    else:
        send(action)


# ---------------------------------------------------------------- commands

def run_read(ns) -> None:
    path = ns.path or ns.file
    if path == "-" or (path is None and ns.text is None
                       and ns.source == "auto" and not sys.stdin.isatty()):
        text = sys.stdin.read()
        if not text.strip():
            print("empty stdin", file=sys.stderr)
            sys.exit(1)
        send("read", text=text)
        return
    if ns.text is not None:
        send("read", text=ns.text)
        return
    if path:
        send("read", source="file", path=str(Path(path).expanduser().resolve()))
        return
    if ns.source == "file":
        if not in_tty():
            spawn_floating("read", "--source", "file")
            return
        picked = pick_file_tui()
        if picked:
            send("read", source="file", path=picked)
        return
    send("read", source=ns.source)


def run_status(waybar: bool) -> None:
    resp = send("status")
    resp.pop("ok", None)
    if waybar:
        print(json.dumps({"text": resp.get("state", "idle")}))
    else:
        print(json.dumps(resp, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lectorctl", description="Control the lector daemon",
        epilog="scripting: `lectorctl read doc.pdf`, `cat notes.md | lectorctl read -`, "
               "`lectorctl read --text 'hello'`, `lectorctl status` (json)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_read = sub.add_parser("read", help="read a document aloud")
    p_read.add_argument("path", nargs="?", default=None,
                        help="file to read, or '-' for stdin")
    p_read.add_argument("--text", default=None, help="read this literal text")
    p_read.add_argument("--source", choices=["auto", "clipboard", "selection", "file"],
                        default="auto")
    p_read.add_argument("--file", dest="file", default=None, help=argparse.SUPPRESS)

    for name in ("summarize", "annotate", "pause", "stop", "next", "keep", "menu"):
        sub.add_parser(name)

    p_status = sub.add_parser("status")
    p_status.add_argument("--waybar", action="store_true")

    ns = parser.parse_args()
    if ns.command == "menu":
        run_menu()
    elif ns.command == "status":
        run_status(ns.waybar)
    elif ns.command == "read":
        run_read(ns)
    else:
        send(ns.command)


if __name__ == "__main__":
    main()
