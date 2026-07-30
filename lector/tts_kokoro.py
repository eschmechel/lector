import re
from pathlib import Path

MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"
MODEL_BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"


class ModelsMissing(RuntimeError):
    pass


class KokoroTTS:
    """Blocking Kokoro synthesis — call synth() from a thread executor."""

    def __init__(self, models_dir: Path, voice: str = "af_heart", speed: float = 1.0):
        self.models_dir = models_dir
        self.voice = voice
        self.speed = speed
        self._kokoro = None

    def models_present(self) -> bool:
        return (self.models_dir / MODEL_FILE).is_file() and (self.models_dir / VOICES_FILE).is_file()

    def _ensure(self):
        if self._kokoro is None:
            if not self.models_present():
                raise ModelsMissing(
                    f"Kokoro model files missing in {self.models_dir} — run install.sh"
                )
            from kokoro_onnx import Kokoro

            self._kokoro = Kokoro(
                str(self.models_dir / MODEL_FILE), str(self.models_dir / VOICES_FILE)
            )
        return self._kokoro

    def synth(self, text: str, out_path: Path) -> Path:
        import soundfile as sf

        kokoro = self._ensure()
        samples, sample_rate = kokoro.create(
            text, voice=self.voice, speed=self.speed, lang="en-us"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), samples, sample_rate)
        return out_path

    def warmup(self) -> None:
        """Load the model and JIT the first inference so the first real read is fast."""
        import tempfile

        if not self.models_present():
            return
        with tempfile.TemporaryDirectory() as td:
            self.synth("Ready.", Path(td) / "warmup.wav")


# Codepoints TTS should never try to vocalize: private-use (nerd-font icons),
# box drawing/geometric shapes, misc symbols/dingbats, and the emoji planes.
_UNSPEAKABLE = re.compile(
    "[\u2500-\u257f"          # box drawing
    "\u25a0-\u25ff"           # geometric shapes
    "\u2600-\u27bf"           # misc symbols + dingbats (incl. \u276f)
    "\ue000-\uf8ff"           # BMP private use (nerd-font icons)
    "\U000f0000-\U000ffffd"   # plane-15 private use (material nerd icons)
    "\U0001f000-\U0001faff]"  # emoji planes
)


def _normalize_for_speech(text: str) -> str:
    """Rewrite constructs the TTS engine mangles into speakable forms."""
    # CLI flags: espeak swallows the dashes, turning "-h, --help" into "h, help"
    text = re.sub(r"(?<![\w-])--(?=[A-Za-z])", "dash dash ", text)
    text = re.sub(r"(?<![\w-])-(?=[A-Za-z]\w*)", "dash ", text)
    text = re.sub(r"(?<![\w-])-(?=\d)", "minus ", text)
    # un-spaced comma lists ("read,summarize,annotate") get rushed into one blob;
    # keep digit,digit intact so 1,000 stays a number
    text = re.sub(r",(?=[^\s\d])", ", ", text)
    # raw URLs are unlistenable — speak just the host
    text = re.sub(r"https?://([^/\s?#]+)\S*", r"link to \1", text)
    return text


def chunk_text(text: str, limit: int = 450, first_limit: int = 140) -> list[str]:
    """Split into TTS-sized chunks on sentence, then word boundaries."""
    text = _UNSPEAKABLE.sub(" ", text)
    text = _normalize_for_speech(text)
    sentences: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())
        if not para:
            continue
        sentences.extend(re.split(r"(?<=[.!?])\s+", para))

    chunks: list[str] = []
    cur = ""
    for s in sentences:
        while len(s) > limit:  # pathological run-on: hard split on spaces
            cut = s.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            piece, s = s[:cut], s[cut:].lstrip()
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(piece)
        if len(cur) + len(s) + 1 > limit and cur:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    chunks = [c for c in chunks if re.search(r"\w", c)]

    # Keep the first chunk short so audio starts fast; later chunks synth during playback.
    if chunks and first_limit and len(chunks[0]) > first_limit:
        head = chunks[0]
        m = re.search(r"^(.{20,%d}[.!?])\s" % first_limit, head)
        cut = m.end(1) if m else max(head.rfind(" ", 0, first_limit), 1)
        chunks[0:1] = [head[:cut].strip(), head[cut:].strip()]
    return chunks
