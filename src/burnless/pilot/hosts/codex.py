from __future__ import annotations

from pathlib import Path
from typing import Iterable
import shutil

from ..core import ContextUsage, HostCapabilities, HostInstallation, HostSession, PilotEvent, _version_for


class CodexAdapter:
    name = "codex"

    def detect(self) -> HostInstallation:
        path = shutil.which("codex")
        return HostInstallation(
            name=self.name,
            command="codex",
            path=path,
            version=_version_for("codex"),
            available=bool(path),
        )

    def capabilities(self) -> HostCapabilities:
        return HostCapabilities(
            can_clear=True,
            can_resume=True,
            supports_hooks=True,
            supports_usage=False,
            transcript_access=False,
            rollout_access=True,
            trust="trusted",
            reset_strategy="respawn",
        )

    def build_interactive_argv(self, root: Path, model: str | None = None, extra_args: Iterable[str] = ()) -> list[str]:
        argv = ["codex", "-C", str(root)]
        if model:
            argv.extend(["--model", model])
        argv.extend(list(extra_args))
        return argv

    def build_fresh_argv(self, root: Path, model: str | None = None, extra_args: Iterable[str] = ()) -> list[str]:
        return self.build_interactive_argv(root, model=model, extra_args=extra_args)

    def normalize_hook_event(self, payload: dict) -> PilotEvent:
        return PilotEvent(host=self.name, host_session_id=payload.get("session_id"), process_instance_id=payload.get("process_instance_id"), event=str(payload.get("hookEventName") or payload.get("event") or "unknown"))

    def locate_session(self, run_id: str) -> HostSession:
        return HostSession(host=self.name, host_session_id=run_id, process_instance_id=run_id)

    def context_usage(self, session: HostSession) -> ContextUsage:
        return ContextUsage(current=None, limit=None, confidence="unknown")

    def is_turn_idle(self, session: HostSession) -> bool:
        return True
