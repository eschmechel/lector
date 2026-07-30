import os
import tomllib
from dataclasses import dataclass
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
    return Config(
        notes_dir=Path(paths.get("notes_dir", "~/Notes/lector")).expanduser(),
        models_dir=Path(paths.get("models_dir", "~/.local/share/lector/models")).expanduser(),
        voice=tts.get("voice", "af_heart"),
        speed=float(tts.get("speed", 1.0)),
        long_doc_words=int(read.get("long_doc_words", 1500)),
        inbox_enabled=bool(inbox.get("enabled", True)),
    )
