"""Microphone capture.

The stream is opened once and held open for the life of the daemon. Opening a
suspended PipeWire node returns silence for the first few hundred milliseconds, so
opening per keypress would eat the first word of every utterance; instead the stream
runs continuously and a gate decides whether blocks are kept.
"""

import contextlib
import os
import subprocess
import threading
import time

import numpy as np


class AudioUnavailable(RuntimeError):
    pass


@contextlib.contextmanager
def _quiet_stderr():
    """ALSA scribbles on stderr during device enumeration; keep the journal readable."""
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


def default_source() -> str:
    try:
        out = subprocess.run(["pactl", "get-default-source"], capture_output=True,
                             text=True, timeout=3)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def bluetooth_default() -> str | None:
    """Name of the default source if it is a Bluetooth device, else None.

    A BT headset flips to HFP the moment it is used for capture: mono, ~16kHz, and
    it degrades simultaneous playback on the same device.
    """
    name = default_source()
    return name if name.startswith(("bluez_input", "bluez_source")) else None


class MicStream:
    def __init__(self, device: str, samplerate: int, blocksize: int, warmup_ms: int):
        self.device = device
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.warmup_s = warmup_ms / 1000.0
        self._stream = None
        self._opened_at = 0.0
        self._lock = threading.Lock()
        self._blocks: list[np.ndarray] = []
        self._collecting = False
        self._level = 0.0

    # ------------------------------------------------------------------ lifecycle

    def _sd(self):
        try:
            import sounddevice as sd
        except (ImportError, OSError) as e:  # OSError: PortAudio missing
            raise AudioUnavailable(f"sounddevice unavailable: {e}") from e
        return sd

    def open(self) -> None:
        if self._stream is not None:
            return
        sd = self._sd()
        try:
            with _quiet_stderr():
                stream = sd.InputStream(
                    device=self.device or None, channels=1,
                    samplerate=self.samplerate, dtype="float32",
                    blocksize=self.blocksize, callback=self._on_block,
                )
                stream.start()
        except Exception as e:  # noqa: BLE001 - PortAudioError and friends
            raise AudioUnavailable(
                f"could not open mic device {self.device!r}: {e}") from e
        self._stream = stream
        self._opened_at = time.monotonic()

    def close(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None

    def reopen(self) -> None:
        """Re-enumerate devices. PortAudio caches the device list at import, so a
        headset that reconnects is invisible until the library is re-initialized."""
        self.close()
        sd = self._sd()
        with contextlib.suppress(Exception), _quiet_stderr():
            sd._terminate()
            sd._initialize()
        self.open()

    # ------------------------------------------------------------------ capture

    def _on_block(self, indata, _frames, _t, _status) -> None:
        block = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean(block * block))) if block.size else 0.0
        # Smooth for the indicator; raw RMS flickers too fast to read.
        self._level = max(rms, self._level * 0.75)
        if time.monotonic() - self._opened_at < self.warmup_s:
            return  # suspended-node warm-up: these blocks are silence
        with self._lock:
            if self._collecting:
                self._blocks.append(block)

    def start_collect(self) -> None:
        self.open()
        with self._lock:
            self._blocks.clear()
            self._collecting = True

    def stop_collect(self) -> np.ndarray:
        with self._lock:
            self._collecting = False
            blocks, self._blocks = self._blocks, []
        if not blocks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(blocks).astype(np.float32)

    def peek_seconds(self) -> float:
        with self._lock:
            return sum(b.size for b in self._blocks) / self.samplerate

    @property
    def collecting(self) -> bool:
        return self._collecting

    @property
    def level(self) -> float:
        """0.0-1.0-ish smoothed input level for the indicator."""
        return min(1.0, self._level * 12.0)
