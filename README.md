# lector

Voice in, voice out, on your own laptop. Hold a key and speak, and polished text lands
at your cursor in whatever app has focus. Hold a different key and describe what you
want to say, and lector writes it — or rewrites the text you have selected. Hit
`Super+R` and whatever you have highlighted gets read aloud.

It is a local subset of what [Willow Voice] and [Clicky] do: dictation and in-place
rewriting, plus read-aloud and document summarizing, with no account and no per-word
billing. Speech recognition and speech synthesis both run on the CPU, so the GPU stays
free for the language model.

## Status

- **P1: read-aloud core** — shipped. Highlight/clipboard/file/inbox → Kokoro → mpv,
  pause/stop/next-section binds, long-doc handling, waybar state.
- **P2: the brain** — shipped. Summarize (`Super+Alt+R`), annotate (`Super+Alt+A`),
  and smart-read via local Ollama, with an optional cloud lane.
- **P3 (current): voice in** — hold-to-talk dictation with [Parakeet TDT 0.6B],
  Scribe (speak intent → prose, or rewrite the selection by voice), a style card and
  per-app tone profiles, spoken-phrase shortcuts, and a dictionary that learns from
  your corrections.
- P4 ask-the-doc · P5 polish — planned. See `docs/PLAN-lector.md`.

## What runs where

Transcription (Parakeet) and synthesis (Kokoro) are always local, on the CPU — your
voice never leaves the machine. The LLM tiers are configurable per mode: cleanup,
summarize, annotate and smart-read default to local Ollama, while **Scribe defaults to
whatever OpenAI-compatible endpoint you configure** because it is the quality- and
latency-sensitive tier. Leave `cloud_base_url` blank to keep everything local; the
cloud lane also falls back to the local model automatically if it is unreachable.

## Install (Arch + Hyprland/HyDE)

```sh
git clone https://github.com/eschmechel/lector ~/Repos/lector
cd ~/Repos/lector
./install.sh    # uv sync, models (~340MB Kokoro + ~671MB Parakeet), config, unit, binds
```

`install.sh` is idempotent. It appends one `source =` line to
`~/.config/hypr/userprefs.conf` (backed up first) and never edits HyDE-managed files —
the chords lector takes over are released with `unbind` from its own config file, so a
HyDE regeneration cannot resurrect them.

Needs `wtype`, `wl-clipboard`, `mpv`, `dunst`, `fzf`, and PipeWire.

## Keybinds

| Chord | Action |
|---|---|
| `Super+H` *(hold)* | dictate — hold to talk, release to insert. Double-tap to latch for long dictation |
| `Super+Shift+H` *(hold)* | Scribe — with text selected, rewrite it by voice; with nothing selected, speak your intent and get finished prose |
| `Super+Alt+C` | teach lector: fix its output, select your version, press this |
| `Super+R` | read highlighted text aloud (falls back to clipboard) |
| `Super+Alt+R` / `Super+Alt+A` | summarize → read / annotate to notes |
| `Super+M` | menu — fzf in a floating window (also a TUI: run `lectorctl menu` in any terminal) |
| `Super+Space` / `Super+Alt+Space` | pause-resume / stop |
| `Super+N` | next section |
| `Super+Alt+K` | keep — saves the last render as audio, or promotes the last dictation to its own note |

Binds use HyDE's `bindd` form, so they show up in the `Super+/` keybind hint. While the
mic is live a notification shows a level meter, so you can see it is hearing you.

## Personalization

Three files, all of them yours to read and edit:

- `~/.config/lector/style.md` — free-form prose describing how you write. Injected into
  the cleanup and Scribe prompts.
- `[style.profiles]` in `config.toml` — tone per application, matched on window class,
  so a terminal gets terse output and a browser gets structured prose.
- `[shortcuts]` in `config.toml` — say a phrase, get an expansion.

Press `Super+Alt+C` after fixing something lector got wrong and it diffs its output
against your correction: short substitutions become dictionary entries (so "Hyprland"
stops coming out as "hyper land"), and the pair is kept as a style example. Everything
it learns lands in `~/.config/lector/learned.json`, which is a plain file you can prune.

## CLI / scripting

```sh
lectorctl dictate --toggle              # start/stop listening (what the menu uses)
lectorctl dictate --clean               # add an LLM cleanup pass to this one
lectorctl dictate --note                # also save this one as its own note
lectorctl scribe --toggle               # rewrite selection, or write from intent
lectorctl correct                       # learn from your fix to the last insertion
lectorctl set dictation_interrupt stop  # or pause, or toggle

lectorctl read doc.pdf                  # read a file
cat notes.md | lectorctl read -         # pipe text in (also: cmd | lectorctl read)
lectorctl read --text "hello there"     # literal text
lectorctl read --source file            # fzf picker over your home dir
lectorctl read --smart doc.md           # LLM-rewritten narration instead of verbatim
lectorctl summarize report.pdf          # structured note -> notes dir + clipboard + read aloud
lectorctl annotate --source selection   # inline margin notes, saved (not spoken)
lectorctl summarize --cloud big.pdf     # force the cloud LLM lane for this call
lectorctl pause | stop | next | keep
lectorctl status                        # json — script-friendly
```

Hold-to-talk is a press bind plus a `bindr` release bind. Hyprland does not always fire
the release when you let go of the modifier first, so pressing again finalizes the
capture — the chord degrades to press-to-start / press-to-stop rather than hanging, and
a watchdog closes it after `max_hold_s` regardless. Set `hold_mode = false` for a plain
toggle.

**Bluetooth headsets.** Every reconnect creates a new PipeWire node under the same
device name, so a capture stream bound to the old one stays open and records silence.
lector watches PipeWire and rebinds when the default source moves, and if a capture
still comes back as pure digital silence it rebinds and tells you to try again. Put your
AirPods down and pick them up as often as you like.

**Text is typed, not pasted.** Synthesizing a paste chord is the obvious optimization,
and it does not work here: `ctrl+shift+v` through `wtype` was measured inserting nothing
at all while still leaving both modifiers logically held — after which the next Escape
read as `ctrl+shift+Escape` and launched the system monitor. Typing is exact, needs no
modifiers, and costs about 9ms per character. You can opt an app back into pasting via
`[inject.chords]`, but a chord with two or more modifiers is refused, because that is
the combination that strands them.

**Injection waits for your hands.** Text goes in moments after you release a chord that
begins with Super, so if Super is still down then every typed character is a
`Super+<key>` shortcut — dictating "question mark" jumped to workspace 10, because
`Super+0` is bound to it. lector reads physical key state from `/dev/input` and waits
for modifiers to come up first. That costs ~3ms when your hands are already off, needs
membership of the `input` group, and is skipped harmlessly without it.

## Smoke

```sh
./smoke.sh    # imports, ingest, TTS render, TTS->STT roundtrip, daemon ping
```

## License

MIT. Model weights carry their own terms: Kokoro-82M is Apache-2.0, and **NVIDIA
Parakeet TDT 0.6B v2 is CC-BY-4.0** — attribution to NVIDIA is required if you
redistribute anything derived from it.

[Kokoro-82M]: https://github.com/thewh1teagle/kokoro-onnx
[Parakeet TDT 0.6B]: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2
[Willow Voice]: https://willowvoice.com/
[Clicky]: https://github.com/farzaa/clicky
