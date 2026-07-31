#!/usr/bin/env bash
# lector installer — idempotent. Arch + Hyprland (HyDE) assumed.
set -euo pipefail
cd "$(dirname "$0")"
REPO="$(pwd)"
MODELS_DIR="$HOME/.local/share/lector/models"
MODEL_URL_BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
USERPREFS="${XDG_CONFIG_HOME:-$HOME/.config}/hypr/userprefs.conf"

step() { printf '\n==> %s\n' "$*"; }

step "uv sync (python deps)"
uv sync

step "cli symlinks -> ~/.local/bin"
mkdir -p "$HOME/.local/bin"
ln -sf "$REPO/.venv/bin/lectorctl" "$HOME/.local/bin/lectorctl"
ln -sf "$REPO/.venv/bin/lectord" "$HOME/.local/bin/lectord"

step "data dirs + config"
mkdir -p "$HOME/Notes/lector"/{inbox,notes,audio,index}
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/lector"
mkdir -p "$CONF_DIR"
[[ -f "$CONF_DIR/config.toml" ]] || cp config.example.toml "$CONF_DIR/config.toml"

step "kokoro models (~340MB, one-time)"
mkdir -p "$MODELS_DIR"
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
    if [[ ! -f "$MODELS_DIR/$f" ]]; then
        echo "downloading $f ..."
        curl -fL --progress-bar -o "$MODELS_DIR/$f.part" "$MODEL_URL_BASE/$f"
        mv "$MODELS_DIR/$f.part" "$MODELS_DIR/$f"
    else
        echo "$f already present"
    fi
done

step "systemd user service"
mkdir -p "$HOME/.config/systemd/user"
cp systemd/lector.service "$HOME/.config/systemd/user/lector.service"
systemctl --user daemon-reload
systemctl --user enable --now lector.service

step "ollama (local LLM for summarize/annotate/smart-read)"
if command -v ollama >/dev/null; then
    cp systemd/ollama.service "$HOME/.config/systemd/user/ollama.service"
    systemctl --user daemon-reload
    systemctl --user enable --now ollama.service
    for i in {1..20}; do curl -sf http://127.0.0.1:11434/api/version >/dev/null && break; sleep 0.5; done
    ollama list | grep -q "qwen3:4b-instruct" || ollama pull qwen3:4b-instruct
    pacman -Q ollama-cuda >/dev/null 2>&1 || \
        echo "WARN: only CPU ollama installed — 'sudo pacman -S ollama-cuda' for GPU speed"
else
    echo "WARN: ollama not installed (pacman -S ollama) — LLM modes will be unavailable"
fi

step "hyprland binds (copied to ~/.config/hypr, sourced from userprefs.conf)"
# copy rather than source from the repo: the repo may live on a mount that
# isn't up yet when Hyprland parses config at login
HYPR_BINDS="${XDG_CONFIG_HOME:-$HOME/.config}/hypr/lector-binds.conf"
cp hypr/lector-binds.conf "$HYPR_BINDS"
SOURCE_LINE="source = $HYPR_BINDS"
if [[ -f "$USERPREFS" ]] && grep -qE "source *=.*Repos/lector/hypr/lector-binds" "$USERPREFS"; then
    sed -i "s|^source *=.*Repos/lector/hypr/lector-binds.conf|source = $HYPR_BINDS|" "$USERPREFS"
    echo "migrated old repo-path source line"
elif [[ -f "$USERPREFS" ]] && ! grep -qF "lector-binds.conf" "$USERPREFS"; then
    cp "$USERPREFS" "$USERPREFS.bak-lector"
    printf '\n# lector read-aloud daemon\n%s\n' "$SOURCE_LINE" >> "$USERPREFS"
    echo "appended source line (backup at $USERPREFS.bak-lector)"
elif [[ ! -f "$USERPREFS" ]]; then
    echo "WARN: $USERPREFS not found — add manually: $SOURCE_LINE"
else
    echo "source line already present"
fi
command -v hyprctl >/dev/null && hyprctl reload >/dev/null && echo "hyprctl reloaded" || true

step "done"
echo "Try: copy some text, then Super+R. Menu: Super+M. Smoke: ./smoke.sh"
