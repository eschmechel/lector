import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "lector" / "config.toml"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "lector"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "lector"

CTL_SOCKET = RUNTIME_DIR / "ctl.sock"
MPV_SOCKET = RUNTIME_DIR / "mpv.sock"
STATE_FILE = RUNTIME_DIR / "state.json"
RENDER_DIR = CACHE_DIR / "render"

DOC_SUFFIXES = {".md", ".txt", ".pdf"}

# Text is typed, not pasted. A synthesized ctrl+shift+v was measured inserting
# nothing at all while still stranding both modifiers in the compositor — see the
# module docstring in inject.py. Per-app overrides may name a chord instead, but
# one with two or more modifiers is refused because that is what strands them.
DEFAULT_INJECT_METHODS: dict[str, str] = {}

# Scribe defaults to the cloud lane (D54): it is the quality- and latency-sensitive
# tier, and the Aperture gateway is flat-rate. Everything else stays local.
DEFAULT_LANES = {"scribe": "cloud", "scribe_rewrite": "cloud"}


@dataclass
class Config:
    notes_dir: Path
    models_dir: Path
    voice: str = "af_heart"
    speed: float = 1.0
    long_doc_words: int = 1500
    inbox_enabled: bool = True
    picker_dirs: list[Path] = field(default_factory=lambda: [Path("~").expanduser()])
    picker_exclude: list[str] = field(default_factory=lambda: ["node_modules", "__pycache__"])
    picker_limit: int = 4000
    llm_provider: str = "local"
    llm_local_base_url: str = "http://127.0.0.1:11434"
    llm_local_model: str = "qwen3:4b-instruct"
    llm_cloud_base_url: str = ""
    llm_cloud_model: str = ""
    llm_lanes: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_LANES))
    llm_cloud_fallback_local: bool = True

    # --- dictation (P3) ---
    mic_device: str = "pipewire"
    mic_sample_rate: int = 16000
    mic_block_ms: int = 100
    mic_warmup_ms: int = 300
    warn_bluetooth: bool = True
    stt_model: str = "nemo-parakeet-tdt-0.6b-v2"
    stt_quantization: str = "int8"
    stt_threads: int = 8
    stt_max_segment_s: float = 24.0
    clean_by_default: bool = False
    hold_mode: bool = True
    max_hold_s: float = 120.0
    latch_window_ms: int = 400
    dictation_interrupt: str = "pause"  # "pause" | "stop"

    # --- injection (P3) ---
    inject_default_chord: str = "type"
    inject_chords: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_INJECT_METHODS))
    inject_restore_clipboard: bool = True
    inject_copy_to_clipboard: bool = True
    inject_type_delay_ms: int = 1

    # --- style (P3) ---
    style_card: Path = field(
        default_factory=lambda: Path("~/.config/lector/style.md").expanduser())
    style_profiles: dict[str, str] = field(default_factory=dict)
    shortcuts: dict[str, str] = field(default_factory=dict)
    vocabulary: list[str] = field(default_factory=list)

    @property
    def inbox_dir(self) -> Path:
        return self.notes_dir / "inbox"

    @property
    def audio_dir(self) -> Path:
        return self.notes_dir / "audio"

    @property
    def notes_out_dir(self) -> Path:
        return self.notes_dir / "notes"

    @property
    def dictation_dir(self) -> Path:
        return self.notes_dir / "dictation"

    @property
    def mic_blocksize(self) -> int:
        return int(self.mic_sample_rate * self.mic_block_ms / 1000)

    def lane_for(self, mode: str) -> str:
        """Which LLM lane a mode runs on. Explicit per-mode wins, else the default."""
        return self.llm_lanes.get(mode, self.llm_provider)

    def ensure_dirs(self) -> None:
        for d in (self.notes_dir, self.inbox_dir, self.audio_dir, self.notes_out_dir,
                  self.dictation_dir, RUNTIME_DIR, RENDER_DIR):
            d.mkdir(parents=True, exist_ok=True)


def load() -> Config:
    raw: dict = {}
    if CONFIG_PATH.exists():
        raw = tomllib.loads(CONFIG_PATH.read_text())
    paths = raw.get("paths", {})
    tts = raw.get("tts", {})
    read = raw.get("read", {})
    inbox = raw.get("inbox", {})
    picker = raw.get("picker", {})
    llm = raw.get("llm", {})
    dictation = raw.get("dictation", {})
    inject = raw.get("inject", {})
    style = raw.get("style", {})

    chords = dict(DEFAULT_INJECT_METHODS)
    chords.update(inject.get("chords", {}))
    lanes = dict(DEFAULT_LANES)
    lanes.update(llm.get("lanes", {}))

    return Config(
        notes_dir=Path(paths.get("notes_dir", "~/Notes/lector")).expanduser(),
        models_dir=Path(paths.get("models_dir", "~/.local/share/lector/models")).expanduser(),
        voice=tts.get("voice", "af_heart"),
        speed=float(tts.get("speed", 1.0)),
        long_doc_words=int(read.get("long_doc_words", 1500)),
        inbox_enabled=bool(inbox.get("enabled", True)),
        picker_dirs=[Path(d).expanduser() for d in picker.get("dirs", ["~"])],
        picker_exclude=list(picker.get("exclude", ["node_modules", "__pycache__"])),
        picker_limit=int(picker.get("limit", 4000)),
        llm_provider=llm.get("provider", "local"),
        llm_local_base_url=llm.get("local_base_url", "http://127.0.0.1:11434"),
        llm_local_model=llm.get("local_model", "qwen3:4b-instruct"),
        llm_cloud_base_url=llm.get("cloud_base_url", ""),
        llm_cloud_model=llm.get("cloud_model", ""),
        llm_lanes=lanes,
        llm_cloud_fallback_local=bool(llm.get("cloud_fallback_local", True)),
        mic_device=dictation.get("device", "pipewire"),
        mic_sample_rate=int(dictation.get("sample_rate", 16000)),
        mic_block_ms=int(dictation.get("block_ms", 100)),
        mic_warmup_ms=int(dictation.get("warmup_ms", 300)),
        warn_bluetooth=bool(dictation.get("warn_bluetooth", True)),
        stt_model=dictation.get("model", "nemo-parakeet-tdt-0.6b-v2"),
        stt_quantization=dictation.get("quantization", "int8"),
        stt_threads=int(dictation.get("threads", 8)),
        stt_max_segment_s=float(dictation.get("max_segment_s", 24.0)),
        clean_by_default=bool(dictation.get("clean_by_default", False)),
        hold_mode=bool(dictation.get("hold_mode", True)),
        max_hold_s=float(dictation.get("max_hold_s", 120.0)),
        latch_window_ms=int(dictation.get("latch_window_ms", 400)),
        dictation_interrupt=read.get("dictation_interrupt", "pause"),
        inject_default_chord=inject.get("method", inject.get("default_chord", "type")),
        inject_chords=chords,
        inject_restore_clipboard=bool(inject.get("restore_clipboard", True)),
        inject_copy_to_clipboard=bool(inject.get("copy_to_clipboard", True)),
        inject_type_delay_ms=int(inject.get("type_delay_ms", 1)),
        style_card=Path(style.get("card", "~/.config/lector/style.md")).expanduser(),
        style_profiles=dict(style.get("profiles", {})),
        shortcuts=dict(raw.get("shortcuts", {})),
        vocabulary=list(dictation.get("vocabulary", [])),
    )
