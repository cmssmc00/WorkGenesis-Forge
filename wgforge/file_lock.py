"""Process locks for local macOS/Linux campaign coordination."""
from contextlib import contextmanager
from pathlib import Path
import fcntl

from .common import ForgeError


@contextmanager
def file_lock(path, *, blocking=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the inode: unlinking a flock file can let two processes own different
    # locks at the same path. The OS releases ownership if a process exits.
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise ForgeError("Another process is using " + str(path.parent)) from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
