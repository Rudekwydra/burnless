from __future__ import annotations
import contextlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_STATE: dict = {
    "project": "Project",
    "last_delegation": None,
    "next": None,
    "delegation_counter": 0,
    "active_tier": None,  # None = auto routing; "gold"|"silver"|"bronze" = sticky
    "brain_model": None,
    "updated_at": None,
    "last_activity_ts": None,
    "next_keepalive_ts": None,
    "keepalive_last_ts": None,
    "keepalive_last_status": None,
    "keepalive_mode": "",  # 'api_key' | 'subscription' | ''
    "keepalive_ttl_window_s": 0,  # 3000 ou 270
}


def load(path: Path) -> dict:
    if not path.exists():
        return dict(DEFAULT_STATE)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    for k, v in DEFAULT_STATE.items():
        data.setdefault(k, v)
    return data


def save(path: Path, state: dict) -> None:
    import os
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def next_delegation_id(state: dict) -> str:
    state["delegation_counter"] = int(state.get("delegation_counter", 0)) + 1
    return f"d{state['delegation_counter']:03d}"


@contextlib.contextmanager
def _exclusive_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
        with open(lock_path, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except ImportError:
        # Windows: spin-lock via O_CREAT | O_EXCL
        import os
        acquired = False
        for _ in range(150):
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                acquired = True
                break
            except FileExistsError:
                time.sleep(0.02)
        if not acquired:
            raise RuntimeError(f"Could not acquire state lock: {lock_path}")
        try:
            yield
        finally:
            lock_path.unlink(missing_ok=True)


def alloc_delegation_id(state_path: Path) -> str:
    """Atomically allocate and persist the next delegation ID (race-safe)."""
    lock_path = state_path.with_name("state.lock")
    with _exclusive_lock(lock_path):
        st = load(state_path)
        did = next_delegation_id(st)
        save(state_path, st)
    return did


def update_locked(state_path: Path, mutator) -> dict:
    """Race-safe read-modify-write: load state under lock, apply mutator(state),
    save, return the new state. Pairs with the atomic save() above so parallel
    workers never observe a torn file and never lose each other's writes."""
    lock_path = state_path.with_name("state.lock")
    with _exclusive_lock(lock_path):
        st = load(state_path)
        mutator(st)
        save(state_path, st)
    return st


def touch_activity(state: dict, idle_threshold_s: int = 3000, now: datetime | None = None) -> None:
    """Mutate state in-place recording last activity and scheduling next keepalive.

    Caller is responsible for calling save() after this.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    state["last_activity_ts"] = now.isoformat()
    state["next_keepalive_ts"] = (now + timedelta(seconds=idle_threshold_s)).isoformat()
