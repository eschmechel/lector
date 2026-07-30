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


def chunk_text(text: str, limit: int = 450) -> list[str]:
    """Split into TTS-sized chunks on sentence, then word boundaries."""
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
    return [c for c in chunks if re.search(r"\w", c)]
