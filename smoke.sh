#!/usr/bin/env bash
# Fast end-to-end sanity gate. Reports "smoke: N/N" on the last line.
set -uo pipefail
cd "$(dirname "$0")"
total=0 pass=0

run() { # run <name> <cmd...>
    local name="$1"; shift
    total=$((total + 1))
    if "$@" >/tmp/lector-smoke-$$.log 2>&1; then
        pass=$((pass + 1)); echo "ok    $name"
    else
        echo "FAIL  $name"; sed 's/^/      /' /tmp/lector-smoke-$$.log | tail -15
    fi
}

run "selftest (imports, ingest, chunker, TTS, STT)" uv run python -m lector.selftest

if systemctl --user is-active --quiet lector.service; then
    run "daemon status roundtrip" .venv/bin/lectorctl status
    # Idle stop is a no-op, but it proves the voice commands are wired end to end.
    run "dictation command roundtrip" .venv/bin/lectorctl dictate --stop
    run "runtime option roundtrip" .venv/bin/lectorctl set dictation_interrupt pause
else
    echo "note  lector.service not active — daemon roundtrips skipped"
fi

rm -f /tmp/lector-smoke-$$.log
echo "smoke: $pass/$total"
[[ $pass -eq $total ]]
