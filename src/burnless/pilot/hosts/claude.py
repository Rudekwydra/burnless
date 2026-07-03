from __future__ import annotations

from pathlib import Path
from typing import Iterable
import shutil

from ..core import ContextUsage, HostCapabilities, HostInstallation, HostSession, PilotEvent, _version_for


class ClaudeAdapter:
    name = "claude"

    def detect(self) -> HostInstallation:
        path = shutil.which("claude")
        return HostInstallation(
            name=self.name,
            command="claude",
            path=path,
            version=_version_for("claude"),
            available=bool(path),
        )

    def capabilities(self) -> HostCapabilities:
        return HostCapabilities(
            can_clear=True,
            can_resume=True,
            supports_hooks=True,
            supports_usage=True,
            transcript_access=True,
            rollout_access=False,
            trust="trusted",
            reset_strategy="native-clear",
        )

    def build_interactive_argv(self, root: Path, model: str | None = None, extra_args: Iterable[str] = ()) -> list[str]:
        # Claude Code has no `-C` cwd flag (that's codex); the pilot sets cwd
        # on spawn instead.
        argv = ["claude"]
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
