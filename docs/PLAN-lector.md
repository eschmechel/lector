# PLAN — lector

Local read-aloud / annotate / voice-Q&A daemon. NotebookLM-at-home for one laptop:
hotkey-driven, waybar-fronted, fully local by default.

Planned 2026-07-30 via interview. Target machine: Arch + Hyprland (HyDE),
i9-13900H / 32GB RAM / RTX 4060 Max-Q 8GB.

## Decisions

| # | Decision |
|---|---|
| D1 | Bidirectional: TTS out + voice input in |
| D2 | Arch + Hyprland (HyDE); waybar widget + keybinds |
| D3 | Default action = read-aloud verbatim; annotate/summarize are optional modes |
| D4 | Inputs: clipboard, primary selection, file picker, drop folder |
| D5 | Mode via: direct keybinds + rofi menu + widget sticky toggle |
| D6 | STT = Parakeet TDT 0.6B (final transcript, CPU) + Moonshine v2 Medium (live streaming preview, CPU) |
| D7 | TTS = Kokoro-82M default (CPU, streaming) + Chatterbox explicit HQ-render mode (GPU) |
| D8 | Laptop is the host; all inference local-first, zero VRAM for TTS/STT (CPU) |
| D9 | Text outputs → ~/Notes/lector/ as .md AND clipboard |
| D10 | Audio: play by default; "keep that" action saves the render |
| D11 | Long docs: threshold → notification asks read-all / section-by-section / cancel (+ summarize once P2 lands) |
| D12 | Resident daemon; mpv IPC; pause/resume/stop/next-section binds |
| D13 | Kokoro default + Chatterbox HQ as explicit action (GPU-guarded vs Ollama) |
| D14/D17 | Dual STT: Moonshine streams live partials, Parakeet finalizes on release |
| D15 | Build everything in-repo (no Handy dependency) |
| D16 | Brain = qwen3:4b via local Ollama; cloud lane = OpenAI-compatible config |
| D18 | Cloud lane points at Aperture (homelab AI gateway) over the tailnet; endpoint/key live in gitignored .env |
| D19 | Repo: github.com/eschmechel/lector, local ~/Repos/lector |
| D20 | Q&A grounding = RAG-lite: chunk → embed (nomic-embed-text via Ollama) → top-k |
| D21 | Plan committed as docs/PLAN-lector.md; no AI co-author trailers on commits |
| D22 | Approval: build P0→P1, checkpoint, then P2+ |
| D23 | Summarize (structured note) and Annotate (inline margin notes) are separate modes |
| D24 | Dictation → clipboard + note AND types into focused window via wtype |
| D25 | Public repo, MIT; tailnet details only in .env |
| D26 | Binds sourced from userprefs.conf; mpv-mpris optional for media-key control |
| D27 | Binds use HyDE `bindd` + description so they appear in the Super+/ hint viewer (hint reads `hyprctl binds -j`) |
| D28 | Existing `Super+F → ~/bin/stt-medium-toggle` (keybindings.conf:221) is removed at P3 when lector dictation replaces it; dead `Super+H → voxd` bind is taken over at P3 |
| D29 | No rofi/wofi: the menu is fzf in a floating kitty window (`lector-menu` class, windowrules in lector-binds.conf), tty-aware — inline TUI when run from a terminal |
| D30 | File picker matches: fzf over recent docs (fd across ~/Notes, ~/Documents, ~/Downloads, ~/Repos); zenity dropped |
| D31 | First-class scripting surface: `lectorctl read <path|->`, stdin piping, `--text`, json `status` |

## Architecture

One Python daemon (`lectord`, systemd user service) owns TTS pipeline, mpv playback,
doc ingest, and (later) mic + doc index. A thin CLI (`lectorctl`) talks to it over a
unix socket — Hyprland binds, the waybar module, and the rofi menu are all `lectorctl`
calls. Kokoro/Parakeet/Moonshine run in-process on CPU (~2GB RAM total, zero VRAM),
so the GPU belongs entirely to Ollama.

Data lives in `~/Notes/lector/`: `inbox/` (drop folder), `notes/`, `audio/` (kept
renders), `index/` (RAG store, P4).

## Keybinds (all verified free on the user's config)

| Chord | Action | Phase |
|---|---|---|
| Super+R | read aloud (clipboard/selection auto) | P1 |
| Super+Alt+R | summarize → read | P2 |
| Super+Alt+A | annotate (inline margin notes) | P2 |
| Super+M | rofi menu | P1 |
| Super+H | dictation toggle (takes over old voxd bind) | P3 |
| Super+Alt+H | ask-the-doc voice question | P4 |
| Super+Space / Super+Alt+Space | pause-resume / stop | P1 |
| Super+N / Super+Alt+K | next section / keep-that | P1 |

## Phases

1. **P0 scaffold** — repo, uv project, config, systemd unit, smoke.sh, GitHub remote. ✅
2. **P1 read-aloud core** (usable v0) — capture → ingest → Kokoro → mpv; pause/stop/next;
   waybar state; long-doc threshold flow incl. section-by-section. **← checkpoint here**
3. **P2 brain** — summarize/annotate via qwen3:4b; notes + clipboard outputs; Aperture
   cloud profile behind config.
4. **P3 voice in** — push-to-talk dictation: Moonshine live partials, Parakeet final
   transcript → note + clipboard + wtype into focused window. Remove Super+F bind (D28).
5. **P4 ask-the-doc** — RAG-lite index on ingest; hold-to-ask → answer → spoken + saved.
6. **P5 polish** — Chatterbox HQ-render + GPU guard, waybar wiring into HyDE layout,
   mpv-mpris, config-driven keybind regen.

## Non-goals (v1)

Fish Audio API (dropped — VRAM/license math), Windows support, voice cloning, two-host
podcast dialogue (v2 candidate), Handy integration, mobile/sync, non-English dictation.

## Risks

- PDF section detection is heuristic; falls back to per-page sections.
- Chatterbox vs Ollama GPU contention — HQ mode refuses or unloads the LLM first.
- Notification actions rely on dunst; rofi fallback if needed.
- Kokoro text normalization on weird docs (tables/code) — stripped pre-TTS, imperfect
  prosody expected.
- Moonshine live preview is English-only (MIT tier); Parakeet v3 can go multilingual.
