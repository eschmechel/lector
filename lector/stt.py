"""Speech-to-text.

Parakeet TDT 0.6B v2 (int8) via onnx-asr. It beats Whisper large-v3 on the Open ASR
leaderboard (6.05 vs 7.44 avg WER) and is several times faster than faster-whisper
small on CPU, for ~70MB of Python dependencies instead of NeMo's multi-gigabyte
torch+CUDA tree. CPU-only, so the GPU stays free for Ollama.

The ONNX export uses full attention, so one recognize() call is bounded to roughly
20-30s of audio. Longer captures (latched dictation) are segmented with Silero VAD.
"""

from pathlib import Path
from typing import Protocol

import numpy as np

HF_CACHE = Path("~/.cache/huggingface/hub").expanduser()


class SttUnavailable(RuntimeError):
    pass


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str: ...
    def load(self) -> None: ...
    def available(self) -> bool: ...


class OnnxAsrTranscriber:
    """The onnx-asr backend. Kept behind this narrow interface so swapping to
    sherpa-onnx (the only realistic route to streaming partials for Parakeet) is a
    one-module change."""

    def __init__(self, model: str, quantization: str, threads: int,
                 max_segment_s: float):
        self.model_name = model
        self.quantization = quantization
        self.threads = threads
        self.max_segment_s = max_segment_s
        self._model = None
        self._vad_adapter = None

    # ------------------------------------------------------------------ loading

    def _session_options(self):
        import onnxruntime as ort

        so = ort.SessionOptions()
        # The 13900H is 6 P-cores + 8 E-cores. Letting ORT fan out across the E-cores
        # is measurably slower than staying on the performance cores.
        so.intra_op_num_threads = self.threads
        so.inter_op_num_threads = 1
        return so

    def load(self) -> None:
        """Blocking: downloads on first use (~671MB int8). Call from a thread."""
        if self._model is not None:
            return
        try:
            import onnx_asr
        except ImportError as e:
            raise SttUnavailable(f"onnx-asr not installed: {e}") from e
        try:
            self._model = onnx_asr.load_model(
                self.model_name, quantization=self.quantization,
                sess_options=self._session_options())
        except Exception as e:  # noqa: BLE001 - network, missing files, bad name
            raise SttUnavailable(
                f"could not load STT model {self.model_name!r}: {e}") from e

    def load_vad(self) -> None:
        """Fetch and build the VAD up front.

        Otherwise the first capture longer than max_segment_s stalls mid-finalize
        downloading Silero — the user is waiting on their text at that moment.
        """
        self.load()
        self._vad()

    def _vad(self):
        if self._vad_adapter is None:
            import onnx_asr

            vad = onnx_asr.load_vad("silero", sess_options=self._session_options())
            self._vad_adapter = self._model.with_vad(vad)
        return self._vad_adapter

    def available(self) -> bool:
        """True if the weights are fully cached, so callers can skip the download.

        A partial download leaves the repo directory in place with the encoder blob
        still `.incomplete` and its symlink absent, so directory existence alone is a
        false positive — check that the encoder resolves and is a plausible size.
        """
        try:
            import onnx_asr  # noqa: F401
        except ImportError:
            return False
        if not HF_CACHE.is_dir():
            return False
        slug = self.model_name.removeprefix("nemo-")
        for repo in HF_CACHE.iterdir():
            if slug not in repo.name:
                continue
            for snap in (repo / "snapshots").glob("*"):
                for enc in snap.glob("encoder-model*.onnx"):
                    # exists() follows the symlink: a dangling one means unfinished.
                    if enc.exists() and enc.stat().st_size > 50_000_000:
                        return True
        return False

    # ------------------------------------------------------------------ inference

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        if audio is None or audio.size == 0:
            return ""
        self.load()
        duration = audio.size / sample_rate
        if duration <= self.max_segment_s:
            return str(self._model.recognize(audio, sample_rate=sample_rate)).strip()

        segments = self._vad().recognize(audio, sample_rate=sample_rate)
        parts = []
        for seg in segments:
            text = getattr(seg, "text", None)
            text = (text if text is not None else str(seg)).strip()
            if text:
                parts.append(text)
        return " ".join(parts)


def build(cfg) -> OnnxAsrTranscriber:
    return OnnxAsrTranscriber(cfg.stt_model, cfg.stt_quantization, cfg.stt_threads,
                              cfg.stt_max_segment_s)
