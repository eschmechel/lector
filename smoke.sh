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

# Probe the socket rather than systemd: a daemon run by hand (from a worktree, say)
# is just as testable, and gating on the unit silently skipped these.
if .venv/bin/lectorctl status >/dev/null 2>&1; then
    run "daemon status roundtrip" .venv/bin/lectorctl status
    # Idle stop is a no-op, but it proves the voice commands are wired end to end.
    run "dictation command roundtrip" .venv/bin/lectorctl dictate --stop
    run "runtime option roundtrip" .venv/bin/lectorctl set dictation_interrupt pause
else
    echo "note  no lector daemon reachable — daemon roundtrips skipped"
fi

rm -f /tmp/lector-smoke-$$.log
echo "smoke: $pass/$total"
[[ $pass -eq $total ]]
