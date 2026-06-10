from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# Execution tools the maestro must NOT use (tool-less by policy). Defs stay present as cache anchor;
# usage is blocked. The maestro only decides + emits delegate lines.
MAESTRO_DISALLOWED = "Edit,Write,Bash,NotebookEdit,Task,WebFetch,WebSearch,Read,Grep,Glob,LS"

# runner(cmd: list[str]) -> dict   (returns parsed claude --output-format json result incl 'usage', 'session_id')
RunnerFn = Callable[[list[str]], dict]


@dataclass
class MaestroSession:
    base_uuid: str                       # the warm maestro base session (cached system+role+tool defs)
    model: str
    claude_bin: str = "claude"
    fork_session_id: Optional[str] = None   # current live fork within a cycle; None => next call forks base
    usages: list[dict] = field(default_factory=list)

    def build_command(self, user_msg: str, *, rewind_capsule: Optional[str] = None) -> list[str]:
        """Construct the claude command for one turn.

        cycle start (fork_session_id is None): fork the cached BASE (--resume base --fork-session);
        if rewind_capsule given, the user_msg is prefixed with the rolling state to re-seed the cycle.
        mid-cycle (fork_session_id set): CONTINUE the same fork (--resume fork, NO --fork-session) so the
        accumulated prefix caches incrementally.
        Tool-less by policy: tool defs PRESENT (do NOT pass --tools ""), execution blocked via --disallowedTools.
        """
        msg = user_msg
        if rewind_capsule:
            msg = f"## State (carry-forward)\n{rewind_capsule}\n\n{user_msg}"
        cmd = [self.claude_bin, "-p", msg, "--model", self.model,
               "--output-format", "json",
               "--disallowedTools", MAESTRO_DISALLOWED,
               "--permission-mode", "bypassPermissions",
               "--strict-mcp-config",
               "--disable-slash-commands",
               "--setting-sources", "project,local",
               "--exclude-dynamic-system-prompt-sections"]
        if self.fork_session_id is None:
            cmd += ["--resume", self.base_uuid, "--fork-session"]
        else:
            cmd += ["--resume", self.fork_session_id]
        return cmd

    def send(self, user_msg: str, *, runner: RunnerFn, rewind_capsule: Optional[str] = None) -> tuple[str, int]:
        """One turn. Returns (response_text, response_tokens). Tracks the live fork id + usage."""
        cmd = self.build_command(user_msg, rewind_capsule=rewind_capsule)
        result = runner(cmd)
        self.fork_session_id = result.get("session_id") or self.fork_session_id
        usage = result.get("usage") or {}
        self.usages.append(usage)
        text = result.get("result", "")
        rtoks = int(usage.get("output_tokens", 0) or 0)
        return text, rtoks

    def rewind(self) -> None:
        """End the current cycle: drop the live fork so the next send() forks the cached BASE fresh."""
        self.fork_session_id = None
