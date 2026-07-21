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
    "next_plan_id": None,
    "next_revision": None,
    "next_updated_at": None,
    "next_source": None,
    "delegation_counter": 0,
    "turn_counter": 0,  # For savings footer per-turn tracking
    "active_tier": None,  # None = auto routing; "gold"|"silver"|"bronze" = sticky
    "brain_model": None,  # legacy persisted key name (kept for on-disk back-compat); represents the Maestro layer
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
    import sys
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[burnless] state save failed for {path}: {e}", file=sys.stderr)
        raise RuntimeError(f"state save failed: {e}") from e
    os.replace(tmp, path)


def save_locked(path: Path, state: dict) -> None:
    """Atomically save state under a process-wide file lock, merging with current disk state."""
    import os
    import sys
    lock_path = path.with_name("state.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
        with open(lock_path, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                current = load(path)
                current.update(state)
                save(path, current)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except ImportError:
        # Windows: spin-lock via O_CREAT | O_EXCL
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
            current = load(path)
            current.update(state)
            save(path, current)
        finally:
            lock_path.unlink(missing_ok=True)


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


def set_next(
    state: dict,
    text: str,
    *,
    plan_id: str,
    revision: int = 1,
    source: str = "worker",
    now: datetime | None = None,
) -> None:
    """Mutate state in-place with a new Next, guarding against out-of-order writes.

    A write for the SAME plan_id with a revision lower than what's already recorded is
    ignored (a late/out-of-order completion must never clobber a newer revision's Next).
    A write for a DIFFERENT plan_id always supersedes the previous plan's Next outright
    (a new plan takes over). Caller is responsible for calling save()/update_locked after
    this, matching every other mutator in this module.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    current_plan_id = state.get("next_plan_id")
    current_revision = state.get("next_revision")
    if plan_id == current_plan_id and current_revision is not None and int(revision) < int(current_revision):
        return
    state["next"] = text or None
    state["next_plan_id"] = plan_id
    state["next_revision"] = int(revision)
    state["next_updated_at"] = now.isoformat()
    state["next_source"] = source


def invalidate_next(state: dict, *, plan_id: str | None = None) -> None:
    """Clear Next. If plan_id is given, only clears when it still matches the current
    next_plan_id (a stale invalidate call must never wipe out a newer, different plan's
    Next that has since taken over). Caller is responsible for calling save()/update_locked."""
    if plan_id is not None and state.get("next_plan_id") != plan_id:
        return
    state["next"] = None
    state["next_plan_id"] = None
    state["next_revision"] = None
    state["next_updated_at"] = None
    state["next_source"] = None
