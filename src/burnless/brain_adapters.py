from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import Protocol


DEFAULT_ANTHROPIC_MODELS = (
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
)


@dataclass(frozen=True)
class BrainCapabilities:
    single_shot: bool = False
    interactive: bool = False
    streaming: bool = False
    delegation: bool = False
    model_switching: bool = False
    workers: bool = False
    native_launcher: bool = False
    planned: bool = False


@dataclass(frozen=True)
class BrainAdapter:
    key: str
    label: str
    kind: str
    command: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    capabilities: BrainCapabilities = BrainCapabilities()
    status: str = "available"
    note: str = ""


class BrainRunner(Protocol):
    adapter: BrainAdapter

    def run_turn(self, message: str) -> dict:
        ...


def current_anthropic_adapter(model: str) -> BrainAdapter:
    models = _unique((*DEFAULT_ANTHROPIC_MODELS, model))
    return BrainAdapter(
        key="anthropic",
        label="Anthropic SDK",
        kind="anthropic",
        models=models,
        capabilities=BrainCapabilities(
            single_shot=True,
            interactive=True,
            streaming=True,
            delegation=True,
            model_switching=True,
        ),
        status="active",
        note="Current Brain adapter; runs in-process through the Anthropic SDK.",
    )


def generic_cli_adapter(name: str, command: str, *, tier: str | None = None) -> BrainAdapter:
    try:
        parts = tuple(shlex.split(command))
    except ValueError:
        parts = (command,)
    provider = _provider_from_command(parts)
    model = _model_from_command(parts)
    label = name or provider or "Generic CLI"
    key = f"cli:{tier or label}".lower().replace(" ", "-")
    note = "Configured worker CLI; available for delegation, not yet wired as Brain chat."
    return BrainAdapter(
        key=key,
        label=label,
        kind="generic_cli",
        command=parts,
        models=(model,) if model else (),
        capabilities=BrainCapabilities(single_shot=True, workers=True),
        status="worker",
        note=note,
    )


def native_adapter(project_root: Path) -> BrainAdapter:
    return BrainAdapter(
        key="native",
        label="Burnless Native",
        kind="native",
        command=("burnless-native", "--project", str(project_root)),
        capabilities=BrainCapabilities(
            single_shot=True,
            interactive=True,
            streaming=True,
            delegation=True,
            model_switching=True,
            workers=True,
            native_launcher=True,
            planned=True,
        ),
        status="planned",
        note="Planned native mode launcher. This stub does not start an interactive process yet.",
    )


def configured_worker_adapters(cfg: dict) -> list[BrainAdapter]:
    adapters: list[BrainAdapter] = []
    for tier, agent in (cfg.get("agents") or {}).items():
        command = str(agent.get("command") or "").strip()
        if not command:
            continue
        adapters.append(
            generic_cli_adapter(str(agent.get("name") or tier), command, tier=str(tier))
        )
    return adapters


def available_brain_models(current_model: str) -> list[str]:
    """Only models usable as Brain today (Anthropic SDK)."""
    return list(current_anthropic_adapter(current_model).models)


def available_maestro_models(cfg: dict, current_model: str) -> list[str]:
    """Legacy alias — returns only Brain-compatible models (Anthropic SDK).
    Worker adapter models are intentionally excluded; use /workers for those.
    """
    return available_brain_models(current_model)


def slash_commands(model: str) -> tuple[str, ...]:
    return (
        "/help",
        "/commands",
        "/maestro",
        f"/maestro {model}",
        "/model",
        f"/model {model}",
        "/workers",
        "/native",
        "/clear",
        "/exit",
    )


def render_commands() -> str:
    return (
        "Commands:\n"
        "  /commands             show commands\n"
        "  /help                 show commands\n"
        "  /maestro              show current Maestro and available models\n"
        "  /maestro <model>      set the Maestro model for this project\n"
        "  /model <model>        alias for /maestro <model>\n"
        "  /workers              show configured worker adapters\n"
        "  /native               show planned Native mode launcher stub\n"
        "  /clear                clear the screen\n"
        "  /exit                 leave the chat\n"
    )


def render_workers(cfg: dict) -> str:
    adapters = configured_worker_adapters(cfg)
    if not adapters:
        return "Workers: none configured in .burnless/config.yaml"
    lines = ["Workers:"]
    for adapter in adapters:
        command = " ".join(adapter.command)
        model = f" model={adapter.models[0]}" if adapter.models else ""
        lines.append(f"  - {adapter.label} [{adapter.kind}]{model}: {command}")
    lines.append("")
    lines.append("These are Worker adapters today; Brain chat still uses the active Brain adapter.")
    return "\n".join(lines)


def render_native(project_root: Path) -> str:
    adapter = native_adapter(project_root)
    command = " ".join(adapter.command)
    return "\n".join(
        [
            f"{adapter.label}: {adapter.status}",
            adapter.note,
            f"Launcher stub: {command}",
            "No interactive Native process was started.",
        ]
    )


def _model_from_command(parts: tuple[str, ...]) -> str | None:
    for i, token in enumerate(parts):
        if token in {"--model", "-m"} and i + 1 < len(parts):
            return parts[i + 1]
        if token.startswith("--model="):
            return token.split("=", 1)[1]
    return None


def _provider_from_command(parts: tuple[str, ...]) -> str:
    if not parts:
        return "Generic CLI"
    exe = Path(parts[0]).name
    if exe == "claude":
        return "Claude CLI"
    if exe == "codex":
        return "Codex CLI"
    return f"{exe} CLI"


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return tuple(out)
