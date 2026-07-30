import argparse
import json
import subprocess
import sys

from . import config as C
from .ipc import request

MENU = [
    ("Read clipboard/selection", ("read", {})),
    ("Read file…", ("read", {"source": "file"})),
    ("Summarize → read  (P2)", ("summarize", {})),
    ("Annotate  (P2)", ("annotate", {})),
    ("Pause / Resume", ("pause", {})),
    ("Next section", ("next", {})),
    ("Keep last audio", ("keep", {})),
    ("Stop", ("stop", {})),
]


def send(cmd: str, **args) -> dict:
    try:
        return request(C.CTL_SOCKET, cmd, **args)
    except (ConnectionRefusedError, FileNotFoundError):
        print("lectord is not running (systemctl --user start lector)", file=sys.stderr)
        sys.exit(1)


def menu() -> None:
    labels = "\n".join(label for label, _ in MENU)
    try:
        out = subprocess.run(
            ["rofi", "-dmenu", "-i", "-p", "lector"],
            input=labels, capture_output=True, text=True,
        ).stdout.strip()
    except FileNotFoundError:
        print("rofi not found", file=sys.stderr)
        sys.exit(1)
    for label, (cmd, args) in MENU:
        if label == out:
            send(cmd, **args)
            return


def status(waybar: bool) -> None:
    resp = send("status")
    if waybar:
        # kept for compatibility; waybar normally uses lector-status.sh directly
        print(json.dumps({"text": resp.get("state", "idle")}))
    else:
        resp.pop("ok", None)
        print(json.dumps(resp, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="lectorctl", description="Control the lector daemon")
    sub = parser.add_subparsers(dest="command", required=True)

    p_read = sub.add_parser("read", help="read a document aloud")
    p_read.add_argument("--source", choices=["auto", "clipboard", "selection", "file"],
                        default="auto")
    p_read.add_argument("--file", dest="path", default=None)

    for name in ("summarize", "annotate", "pause", "stop", "next", "keep", "menu"):
        sub.add_parser(name)

    p_status = sub.add_parser("status")
    p_status.add_argument("--waybar", action="store_true")

    ns = parser.parse_args()
    if ns.command == "menu":
        menu()
    elif ns.command == "status":
        status(ns.waybar)
    elif ns.command == "read":
        send("read", source=ns.source, path=ns.path)
    else:
        send(ns.command)


if __name__ == "__main__":
    main()
