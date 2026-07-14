"""Exclusive lock so only one Path C crypto session writes the paper book."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
from types import TracebackType

from chancetime.utils.paths import project_root

_lock_fd: int | None = None
_lock_path: Path | None = None


class CryptoSessionLock:
    """fcntl exclusive lock on data/crypto_session.lock."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path)
            if path is not None
            else project_root() / "data" / "crypto_session.lock"
        )
        self._fd: int | None = None

    def acquire(self) -> None:
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            # Read pid hint if present
            try:
                hint = Path(self.path).read_text(encoding="utf-8").strip()
            except OSError:
                hint = ""
            os.close(fd)
            raise RuntimeError(
                "Another Path C crypto session already holds the lock "
                f"({self.path}). Stop it first (desktop Stop, or kill that process)."
                + (f" Lock holder hint: {hint}" if hint else "")
            ) from exc
        os.ftruncate(fd, 0)
        os.write(fd, f"pid={os.getpid()}\n".encode())
        self._fd = fd
        global _lock_fd, _lock_path
        _lock_fd = fd
        _lock_path = self.path
        atexit.register(self.release)

    def release(self) -> None:
        import fcntl

        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

    def __enter__(self) -> CryptoSessionLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
