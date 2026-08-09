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

    @property
    def inbox_dir(self) -> Path:
        return self.notes_dir / "inbox"

    @property
    def audio_dir(self) -> Path:
        return self.notes_dir / "audio"

    @property
    def notes_out_dir(self) -> Path:
        return self.notes_dir / "notes"

    def ensure_dirs(self) -> None:
        for d in (self.notes_dir, self.inbox_dir, self.audio_dir, self.notes_out_dir,
                  RUNTIME_DIR, RENDER_DIR):
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
    )
