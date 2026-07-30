import json
import os
import tempfile
from pathlib import Path


class StateFile:
    """Atomic json state for the waybar module and `lectorctl status`."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict = {"state": "idle"}

    def set(self, **kw) -> None:
        self._data = {"state": "idle", **kw} if "state" not in kw else dict(kw)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._data, f)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise

    def get(self) -> dict:
        return dict(self._data)
