#!/usr/bin/env bash
# Emit waybar json for the lector module from the daemon's state file.
state_file="${XDG_RUNTIME_DIR:-/run/user/$UID}/lector/state.json"
if [[ ! -f "$state_file" ]]; then
    echo '{"text": ""}'
    exit 0
fi
python3 - "$state_file" <<'EOF'
import json, sys
try:
    s = json.load(open(sys.argv[1]))
except Exception:
    print(json.dumps({"text": ""})); raise SystemExit
state = s.get("state", "idle")
icons = {"playing": "󰗋", "paused": "󰏤", "processing": "󰑮", "section-wait": "󰒭"}
if state not in icons:
    print(json.dumps({"text": ""})); raise SystemExit
title = s.get("title") or ""
sec, total = s.get("section"), s.get("sections")
tip = f"lector: {state} — {title}"
if sec and total and total > 1:
    tip += f" (section {sec}/{total})"
print(json.dumps({"text": icons[state], "tooltip": tip, "class": state}))
EOF
