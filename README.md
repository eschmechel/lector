# lector

Local read-aloud / annotate / voice-Q&A daemon for Hyprland. NotebookLM-at-home,
one laptop, no cloud required: hit a hotkey, and whatever's in your clipboard —
text, a `.md`/`.txt`/`.pdf` path — gets read aloud through [Kokoro-82M] TTS on CPU.
Later phases add local LLM summarize/annotate, push-to-talk dictation, and
ask-your-doc voice Q&A (see `docs/PLAN-lector.md`).

Everything runs locally: TTS/STT on CPU (zero VRAM), the LLM brain on Ollama.

## Status

- **P1 (current): read-aloud core.** Clipboard/selection/file/inbox → Kokoro → mpv,
  with pause/resume/stop/next-section binds, long-doc handling, waybar state.
- P2 summarize/annotate · P3 dictation · P4 ask-the-doc · P5 polish — planned.

## Install (Arch + Hyprland/HyDE)

```sh
git clone https://github.com/eschmechel/lector ~/Repos/lector
cd ~/Repos/lector
./install.sh          # uv sync, model download (~340MB), config, systemd unit, hypr binds
```

`install.sh` is idempotent. It appends one `source =` line to
`~/.config/hypr/userprefs.conf` (backed up first) and never edits HyDE-managed files.

## Keybinds

| Chord | Action |
|---|---|
| `Super+R` | read clipboard/selection aloud |
| `Super+M` | menu — fzf in a floating kitty window (also a TUI: run `lectorctl menu` in any terminal) |
| `Super+Space` | pause / resume |
| `Super+Alt+Space` | stop |
| `Super+N` | next section |
| `Super+Alt+K` | keep last render as a file in `~/Notes/lector/audio/` |

Binds use HyDE's `bindd` form, so they show up in the `Super+/` keybind hint.
Docs over ~1500 words trigger a notification: read all / section-by-section / cancel.
Drop files into `~/Notes/lector/inbox/` to get a "read this?" notification.

## CLI / scripting

```sh
lectorctl read doc.pdf                  # read a file
cat notes.md | lectorctl read -         # pipe text in (also: cmd | lectorctl read)
lectorctl read --text "hello there"     # literal text
lectorctl read --source file            # fzf file picker (recent docs via fd)
lectorctl pause | stop | next | keep
lectorctl status                        # json — script-friendly
lectorctl menu                          # fzf TUI in your terminal; floating kitty from a bind
```

## Smoke

```sh
./smoke.sh    # fast end-to-end sanity: imports, ingest, TTS render, daemon ping
```

## License

MIT.

[Kokoro-82M]: https://github.com/thewh1teagle/kokoro-onnx
