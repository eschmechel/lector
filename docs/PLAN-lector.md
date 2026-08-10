# PLAN — lector

Local voice-in / read-aloud / annotate daemon. A subset replacement for Willow Voice
(dictation, in-place rewriting) and Clicky (spoken answers about your own material),
built for one Arch + Hyprland (HyDE) laptop and local-first by default.

Planned 2026-07-30, re-scoped 2026-08-09 via interview.
Target machine: i9-13900H / 32GB RAM / RTX 4060 Max-Q 8GB.

## What this is trying to be

Willow = hold a hotkey, speak, polished text lands at your cursor in any app; or speak
your *intent* and it writes the message; or select text, speak a command, and it rewrites
in place. Clicky = hold a hotkey, ask about what you're looking at, hear an answer.

lector aims at the useful subset of both that can run locally: **voice in is the flagship**
(P3), spoken answers over your own documents come after (P4), and read-aloud — the original
NotebookLM-ish framing — remains as a secondary mode that shares the same TTS spine.

## Decisions

| # | Decision |
|---|---|
| D1 | Bidirectional: TTS out + voice input in |
| D2 | Arch + Hyprland (HyDE); waybar widget + keybinds |
| D3 | ~~Default action = read-aloud verbatim~~ **Amended 2026-08-09 by D33: dictation is the default action; read-aloud is a secondary mode** |
| D4 | Inputs: clipboard, primary selection, file picker, drop folder |
| D5 | Mode via: direct keybinds + menu + widget sticky toggle |
| D6 | ~~STT = Parakeet TDT 0.6B + Moonshine v2 Medium~~ **Amended by D38: Parakeet only for v1; Moonshine deferred** |
| D7 | TTS = Kokoro-82M default (CPU, streaming) + Chatterbox explicit HQ-render mode (GPU) |
| D8 | Laptop is the host; inference local-first, zero VRAM for TTS/STT (CPU). **Qualified by D54: Scribe defaults to the cloud lane** |
| D9 | Text outputs → ~/Notes/lector/ as .md AND clipboard |
| D10 | Audio: play by default; "keep that" action saves the render |
| D11 | Long docs: threshold → asks read-all / section-by-section / summarize / cancel. The choice opens in the floating fzf chooser (`ui_ask`), NOT dunst actions — dunst actions are middle-click-only in this setup and proved unanswerable |
| D12 | Resident daemon; mpv IPC; pause/resume/stop/next-section binds |
| D13 | Kokoro default + Chatterbox HQ as explicit action (GPU-guarded vs Ollama) |
| D14/D17 | ~~Dual STT: Moonshine streams partials, Parakeet finalizes~~ **Amended by D38** |
| D15 | Build everything in-repo (no Handy dependency) |
| D16 | Brain = qwen3:4b-instruct via local Ollama; cloud lane = OpenAI-compatible config |
| D18 | Cloud lane points at Aperture (homelab AI gateway) over the tailnet |
| D19 | Repo: github.com/eschmechel/lector, local ~/Repos/lector |
| D20 | Q&A grounding = RAG-lite: chunk → embed (nomic-embed-text via Ollama) → top-k. Confirmed by D36 |
| D21 | Plan committed as docs/PLAN-lector.md; no AI co-author trailers on commits |
| D22 | Approval: checkpoint per phase, user tests before the next starts |
| D23 | Summarize (structured note) and Annotate (inline margin notes) are separate modes |
| D24 | Dictation → clipboard + note AND types into focused window. **Expanded by D34/D40/D41** |
| D25 | Public repo, MIT; tailnet details only in .env |
| D26 | Binds sourced from userprefs.conf; mpv-mpris optional for media-key control |
| D27 | Binds use HyDE `bindd` + description so they appear in the Super+/ hint viewer |
| D28 | `Super+F → ~/bin/stt-medium-toggle` removed at P3; dead `Super+H → voxd` bind taken over |
| D29 | No rofi/wofi: the menu is fzf in a floating kitty window (`lector-menu` class), tty-aware |
| D30 | File picker: whole-home sweep, symlinks followed (`fd -L`), gitignored included, hidden dot-dirs excluded; configurable via `[picker]` |
| D31 | First-class scripting surface: `lectorctl read <path\|->`, stdin piping, `--text`, json `status` |
| D32 | Super+R auto order is selection-first: highlight wins, clipboard is the fallback |
| **D33** | **Dictation is the flagship.** Read-aloud stays on Super+R but demotes. P3 is built to Willow standard, not the thinner original D24 sketch |
| **D34** | Two-tier dictation output: raw transcript on the main bind, LLM-cleaned on a modifier variant |
| **D35** | Scribe ships in P3, not deferred: spoken intent → prose at cursor; selection + spoken command → rewrite in place |
| **D36** | No screen capture. Grounding stays documents and selections, fully local on the text model |
| **D37** | Hold-to-talk (`bind` press + `bindr` release), double-tap latches for long dictation |
| **D38** | Parakeet only for v1. Moonshine live partials deferred, not dropped |
| **D39** | PR #2 (P2 brain) merged before P3 branches — done 2026-08-09 |
| **D40** | Insertion = paste-injection: window class from `hyprctl activewindow -j` picks the paste chord, clipboard saved and restored, `wtype` as fallback |
| **D41** | Transcripts append to a rolling daily log; `keep` is context-sensitive and promotes the last dictation to its own note; also `lectorctl dictate --note` |
| **D42** | Record from the system default source; detect a Bluetooth source and warn/offer to switch — at daemon start and on device change, never mid-utterance |
| **D43** | Mic indicator = waybar state + a replaced dunst notification whose progress bar tracks live RMS |
| **D44** | Scribe is one context-sensitive chord: selection → rewrite in place; no selection → prose from intent |
| **D45** | STT = Parakeet TDT 0.6B **v2 int8 via `onnx-asr`** (~70MB deps, reuses onnxruntime). NeMo rejected: pulls 5–7GB incl. unwanted CUDA. Capture via `sounddevice` on `device="pipewire"` |
| **D46** | Playback during dictation is configurable: `dictation_interrupt = "pause" \| "stop"`, default pause-and-resume, toggleable at runtime |
| **D47** | Style learning is staged: static style card in P3, corpus few-shot in P4 (where the embedding store is being built anyway, and where the dictation corpus will actually exist) |
| **D48** | Corpus sources: dictation log (corrected/kept entries only, never raw output), `~/Notes/**`, an explicitly nominated folder, plus a config list of extra dirs/files |
| **D49** | Per-app style profiles keyed on focused window class |
| **D50** | Saved phrase/shortcut expansion ships in P3 |
| **D51** | Explicit correction bind: fix the text, select it, hit the bind — lector diffs its output against the correction and mines the pair for dictionary + style rules |
| **D52** | `onnx-asr` behind a `Transcriber` interface. sherpa-onnx is the only realistic route to partials for Parakeet (simulated streaming); the swap stays a one-module change |
| **D53** | Cloud lane = Aperture at `http://aperature.tailefc83d.ts.net/v1`, model `deepseek-v4-flash`. **No API key** — tailnet identity authorizes |
| **D54** | Lane split by tier: raw cleanup local (`qwen3:4b-instruct`), Scribe cloud-default with automatic local fallback when the tailnet is unreachable |
| **D55** | Style corpus is local by default; cloud mining only on explicit invocation |

## Architecture

One Python daemon (`lectord`, systemd user service) owns the mic stream, STT, TTS, mpv
playback, doc ingest, text injection, and (P4) the doc index. A thin CLI (`lectorctl`)
talks to it over a unix socket — Hyprland binds, the waybar module, and the fzf menu are
all `lectorctl` calls. Kokoro and Parakeet run in-process on CPU (~2.5GB RAM, zero VRAM),
so the GPU belongs entirely to Ollama.

Data lives in `~/Notes/lector/`: `inbox/` (drop folder), `notes/`, `audio/` (kept
renders), `dictation/` (rolling daily transcript logs), `index/` (RAG store, P4).

## Keybinds

| Chord | Action | Phase |
|---|---|---|
| Super+H (hold) | dictate — hold to talk, double-tap to latch | P3 |
| Super+Shift+H | Scribe — rewrite selection, or write from intent | P3 |
| Super+Alt+C | correct last insertion (feeds dictionary + style) | P3 |
| Super+R | read aloud (selection/clipboard auto) | P1 |
| Super+Alt+R | summarize → read | P2 |
| Super+Alt+A | annotate (inline margin notes) | P2 |
| Super+M | menu | P1 |
| Super+Alt+H | ask-the-doc voice question | P4 |
| Super+Space / Super+Alt+Space | pause-resume / stop | P1 |
| Super+N / Super+Alt+K | next section / keep (audio, or promote last dictation) | P1/P3 |

Removed at P3 (D28): `Super+F → ~/bin/stt-medium-toggle`, `Super+H → voxd`.

## Phases

1. **P0 scaffold** — repo, uv project, config, systemd unit, smoke.sh, GitHub remote. ✅
2. **P1 read-aloud core** — capture → ingest → Kokoro → mpv; pause/stop/next; waybar
   state; long-doc threshold flow. ✅ merged PR #1 2026-07-30
3. **P2 brain** — summarize/annotate/smart-read via qwen3:4b-instruct; notes + clipboard;
   cloud profile behind config. ✅ merged PR #2 2026-08-09
4. **P3 voice in** (the flagship) — persistent mic stream, Parakeet STT, two-tier output,
   paste-injection, Scribe, style card + per-app profiles + shortcuts, correction capture,
   dictation log, mic indicator. Removes the old Super+F bind.
5. **P4 ask-the-doc** — RAG-lite index on ingest; hold-to-ask → answer spoken + saved.
   Also lands the style corpus (D47/D48) on the same embedding store.
6. **P5 polish** — Chatterbox HQ-render + GPU guard, waybar module wired into the HyDE
   layout, mpv-mpris, config-driven keybind regen.

## Non-goals (v1)

Fish Audio API (dropped — VRAM/license math), Windows support, voice cloning, two-host
podcast dialogue, Handy integration, mobile/sync, non-English dictation, screen capture
and vision models (D36), live streaming partials (D38), LoRA/fine-tuning on personal
writing (rejected: small corpora overfit a 4B model and the result can't be read or
debugged the way a prompt can).

## Risks

- **`bindr` on a modified chord** may miss the release if Super is let go before H. Needs
  proving before anything is built on it; mitigated by a daemon-side max-duration timeout
  and accepting a stop from any subsequent trigger.
- **`onnx-asr` caps at 20–30s per call** (full-attention ONNX export). Latched mode must
  segment with Silero VAD rather than accumulating one buffer.
- **First audio after opening a suspended PipeWire node is silence** (~200–300ms). The
  stream stays open across utterances instead of being opened per keypress.
- **PortAudio enumerates devices once at import** — a reconnecting Bluetooth headset needs
  an explicit re-init or the daemon records from a dead node.
- Bluetooth mic drops the headset to HFP, degrading both capture and simultaneous TTS
  playback. D42 warns rather than silently accepting it.
- Model weights are **CC-BY-4.0** (NVIDIA Parakeet) in an MIT repo — attribution required.
- Scribe is noticeably weaker off-tailnet (falls back to the local 4B).
- PDF section detection is heuristic; falls back to per-page sections.
- Chatterbox vs Ollama GPU contention — HQ mode refuses or unloads the LLM first.
- Kokoro text normalization on weird docs (tables/code) — stripped pre-TTS, imperfect
  prosody expected.
