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


SILENCE_EPS = 1e-7      # below this a block is digital silence, not a quiet room
MAX_LEAD_IN_S = 2.0     # give a suspended node this long to start producing


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


def source_fingerprint() -> str:
    """Identity of the current default source, as "<name>#<node id>".

    The node id matters and the name alone is not enough: a Bluetooth headset keeps
    its name across a disconnect/reconnect but comes back as a brand new PipeWire
    node (observed: bluez_input.50:F3:51:D6:F3:53 at #105, then #1495). A capture
    stream bound to the old node stays open and returns silence forever, so the
    change has to be detected on the id.
    """
    name = default_source()
    if not name:
        return ""
    try:
        out = subprocess.run(["pactl", "list", "short", "sources"],
                             capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return name
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] == name:
            return f"{name}#{parts[0]}"
    return name  # default names a source that is not listed: gone or mid-switch


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
        self.opened_for = ""   # source fingerprint this stream was bound to
        self._lock = threading.Lock()
        self._blocks: list[np.ndarray] = []
        self._collecting = False
        self._saw_signal = False
        self._collect_started = 0.0
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
        self.opened_for = source_fingerprint()

    def close(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None

    def reopen(self) -> None:
        """Rebind to whatever the default source is now.

        Both halves are needed: the stream is bound to a PipeWire node that no
        longer exists, and PortAudio caches its device list at import, so a
        reconnected headset stays invisible until the library is re-initialized.
        """
        self.close()
        sd = self._sd()
        with contextlib.suppress(Exception), _quiet_stderr():
            sd._terminate()
            sd._initialize()
        self.open()

    def stale(self) -> bool:
        """True if the default source moved since this stream was opened."""
        if self._stream is None:
            return False
        current = source_fingerprint()
        return bool(current) and current != self.opened_for

    # ------------------------------------------------------------------ capture

    def _on_block(self, indata, _frames, _t, _status) -> None:
        block = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean(block * block))) if block.size else 0.0
        # Smooth for the indicator; raw RMS flickers too fast to read.
        self._level = max(rms, self._level * 0.75)
        if time.monotonic() - self._opened_at < self.warmup_s:
            return  # freshly opened: the first blocks are always silence
        with self._lock:
            if not self._collecting:
                return
            if not self._saw_signal:
                # A PipeWire node that was SUSPENDED keeps returning exact zeros
                # for a while after it is opened, and how long is not predictable
                # — a fixed warm-up either clips the first word or is not enough.
                # Drop the digitally-silent lead-in instead, up to a bound so a
                # genuinely dead node still yields an empty capture we can act on.
                if float(np.max(np.abs(block))) < SILENCE_EPS:
                    if time.monotonic() - self._collect_started < MAX_LEAD_IN_S:
                        return
                else:
                    self._saw_signal = True
            self._blocks.append(block)

    def start_collect(self) -> None:
        self.open()
        with self._lock:
            self._blocks.clear()
            self._collecting = True
            self._saw_signal = False
            self._collect_started = time.monotonic()

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
