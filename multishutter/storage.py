"""Durable manifests and a per-user rig lock independent of the output directory."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import errno
import getpass
import hashlib


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sync_directory(path: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            try:
                os.fsync(descriptor)
            except OSError as error:
                # WSL/DrvFS and some removable filesystems cannot sync directories.
                # Actual I/O, permission and space errors must still be reported.
                if error.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
                    raise
        finally:
            os.close(descriptor)


def write_manifest(path: Path, document: dict) -> None:
    temporary = path.with_suffix(".json.part")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def default_lock_path() -> Path:
    configured = os.environ.get("MULTISHUTTER_LOCK_PATH")
    if configured:
        return Path(configured)
    # All CLI runs for the same OS user lock the same rig, even with different outputs.
    identity = str(os.getuid()) if hasattr(os, "getuid") else hashlib.sha256(getpass.getuser().encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"multishutter-{identity}.lock"


@contextmanager
def rig_lock(path: Path | None = None):
    path = path or default_lock_path()
    with path.open("a+b") as stream:
        try:
            if os.name == "posix":
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("Another MultiShutter process already owns the rig") from error
        try:
            yield
        finally:
            if os.name == "posix":
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            else:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
