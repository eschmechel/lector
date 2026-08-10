"""Are any modifier keys physically held right now?

Synthetic keystrokes combine with whatever the user is still holding. Typing while
Super is down turns every character into a potential Super+<key> shortcut — on this
setup dictating "question mark" jumped to workspace 10, because `Super, 0` is bound
to it. Waiting for the modifiers to come up first is the only way to make injection
safe regardless of how the user releases the chord.

The compositor is no help: `hyprctl devices` reports capsLock and numLock, not live
modifier state. So read it straight from the input devices with EVIOCGKEY, which
reports the current key bitmap without consuming events.

Needs read access to /dev/input/event* (the `input` group). If that is missing,
held() reports False and injection behaves exactly as it did before — degraded, not
broken.
"""

import fcntl
import glob
import os

KEY_MAX = 0x2FF
NBYTES = (KEY_MAX // 8) + 1
# _IOR('E', 0x18, NBYTES)
EVIOCGKEY = (2 << 30) | (NBYTES << 16) | (0x45 << 8) | 0x18

# ctrl / shift / alt / meta, both sides. Deliberately not capslock: it is a latch,
# not something the user is holding down.
MOD_CODES = (29, 42, 54, 56, 97, 100, 125, 126)


class ModifierWatcher:
    def __init__(self) -> None:
        self._fds: list[int] | None = None

    def _open(self) -> list[int]:
        fds = []
        for path in sorted(glob.glob("/dev/input/event*")):
            try:
                fds.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                continue   # no permission, or it vanished
        return fds

    def refresh(self) -> None:
        self.close()
        self._fds = self._open()

    def close(self) -> None:
        for fd in self._fds or []:
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds = None

    def available(self) -> bool:
        if self._fds is None:
            self._fds = self._open()
        return bool(self._fds)

    def held(self) -> list[int]:
        """Key codes of modifiers currently down, across all input devices."""
        if self._fds is None:
            self._fds = self._open()
        down: list[int] = []
        stale = False
        buf = bytearray(NBYTES)
        for fd in self._fds:
            try:
                fcntl.ioctl(fd, EVIOCGKEY, buf)
            except OSError:
                stale = True   # device unplugged mid-read
                continue
            for code in MOD_CODES:
                if buf[code // 8] & (1 << (code % 8)) and code not in down:
                    down.append(code)
        if stale:
            self.refresh()
        return down
