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
class MaestroCapabilities:
    single_shot: bool = False
    interactive: bool = False
    streaming: bool = False
    delegation: bool = False
    model_switching: bool = False
    workers: bool = False
    native_launcher: bool = False
    planned: bool = False


@dataclass(frozen=True)
class MaestroAdapter:
    key: str
    label: str
    kind: str
    command: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    capabilities: MaestroCapabilities = MaestroCapabilities()
    status: str = "available"
    note: str = ""
    api_key_env: str = ""
    base_url: str = ""
    default_model: str = ""
    supports_thinking: bool = False


class MaestroRunner(Protocol):
    adapter: MaestroAdapter

    def run_turn(self, message: str) -> dict:
        ...


def current_anthropic_adapter(model: str) -> MaestroAdapter:
    models = _unique((*DEFAULT_ANTHROPIC_MODELS, model))
    return MaestroAdapter(
        key="anthropic",
        label="Anthropic SDK",
        kind="anthropic",
        models=models,
        capabilities=MaestroCapabilities(
            single_shot=True,
            interactive=True,
            streaming=True,
            delegation=True,
            model_switching=True,
        ),
        status="active",
        note="Current Maestro adapter; runs in-process through the Anthropic SDK.",
        api_key_env="ANTHROPIC_API_KEY",
        supports_thinking=True,
        default_model=DEFAULT_ANTHROPIC_MODELS[0],
    )


def openai_adapter(model: str | None = None) -> MaestroAdapter:
    chosen = model or "gpt-4o"
    return MaestroAdapter(
        key="openai",
        label="OpenAI",
        kind="openai",
        api_key_env="OPENAI_API_KEY",
        supports_thinking=False,
        default_model="gpt-4o",
        models=(chosen, "o3-mini", "o1"),
        capabilities=MaestroCapabilities(
            single_shot=True,
            interactive=True,
            streaming=True,
            delegation=True,
        ),
    )


def gemini_adapter(model: str | None = None) -> MaestroAdapter:
    chosen = model or "gemini-2.5-pro"
    return MaestroAdapter(
        key="gemini",
        label="Gemini",
        kind="gemini",
        api_key_env="GEMINI_API_KEY",
        supports_thinking=False,
        default_model="gemini-2.5-pro",
        models=(chosen, "gemini-2.0-flash"),
        capabilities=MaestroCapabilities(
            single_shot=True,
            interactive=True,
            streaming=True,
            delegation=True,
        ),
    )


def openrouter_adapter(model: str | None = None) -> MaestroAdapter:
    chosen = model or "anthropic/claude-sonnet-4"
    return MaestroAdapter(
        key="openrouter",
        label="OpenRouter",
        kind="openrouter",
        api_key_env="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        supports_thinking=False,
        default_model="anthropic/claude-sonnet-4",
        models=(chosen,),
        capabilities=MaestroCapabilities(
            single_shot=True,
            interactive=True,
            streaming=True,
            delegation=True,
        ),
    )


def load_adapter(cfg: dict, model_hint: str) -> MaestroAdapter:
    kind = cfg.get("maestro_adapter") or cfg.get("brain_adapter", "anthropic")
    if kind == "anthropic":
        return current_anthropic_adapter(model_hint)
    if kind == "openai":
        return openai_adapter(model_hint)
    if kind == "gemini":
        return gemini_adapter(model_hint)
    if kind == "openrouter":
        return openrouter_adapter(model_hint)
    raise NotImplementedError(f"Unknown brain_adapter kind: {kind!r}")


def generic_cli_adapter(name: str, command: str, *, tier: str | None = None) -> MaestroAdapter:
    try:
        parts = tuple(shlex.split(command))
    except ValueError:
        parts = (command,)
    provider = _provider_from_command(parts)
    model = _model_from_command(parts)
    label = name or provider or "Generic CLI"
    key = f"cli:{tier or label}".lower().replace(" ", "-")
    note = "Configured worker CLI; available for delegation, not yet wired as Maestro chat."
    return MaestroAdapter(
        key=key,
        label=label,
        kind="generic_cli",
        command=parts,
        models=(model,) if model else (),
        capabilities=MaestroCapabilities(single_shot=True, workers=True),
        status="worker",
        note=note,
    )


def native_adapter(project_root: Path) -> MaestroAdapter:
    return MaestroAdapter(
        key="native",
        label="Burnless Native",
        kind="native",
        command=("burnless-native", "--project", str(project_root)),
        capabilities=MaestroCapabilities(
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


def configured_worker_adapters(cfg: dict) -> list[MaestroAdapter]:
    adapters: list[MaestroAdapter] = []
    for tier, agent in (cfg.get("agents") or {}).items():
        command = str(agent.get("command") or "").strip()
        if not command:
            continue
        adapters.append(
            generic_cli_adapter(str(agent.get("name") or tier), command, tier=str(tier))
        )
    return adapters


def available_maestro_models(current_model: str) -> list[str]:
    """Only models usable as Maestro today (Anthropic SDK)."""
    return list(current_anthropic_adapter(current_model).models)



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
        "/keepalive",
        "/keepalive status",
        "/keepalive on",
        "/keepalive off",
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
        "  /keepalive [status]   show keepalive daemon status\n"
        "  /keepalive on|off     enable or disable keepalive for this session\n"
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
    lines.append("These are Worker adapters today; Maestro chat still uses the active Maestro adapter.")
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
