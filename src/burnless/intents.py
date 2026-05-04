from __future__ import annotations

import re
from dataclasses import dataclass


DELEGATION_ID = r"(d\d{3,})"


@dataclass(frozen=True)
class Intent:
    kind: str
    args: tuple[str, ...] = ()
    raw: str = ""


def parse(text: str) -> Intent:
    raw = text.strip()
    lowered = raw.lower().strip()
    lowered = lowered.lstrip("/")

    if lowered in {"q", "quit", "exit", "sair"}:
        return Intent("exit", raw=raw)
    if lowered in {"help", "ajuda"}:
        return Intent("help", raw=raw)
    if lowered in {"status", "ver status", "mostrar status"}:
        return Intent("status", raw=raw)
    if lowered in {"metrics", "metricas", "métricas", "mostrar métricas", "mostrar metricas", "burnless tokens"}:
        return Intent("metrics", raw=raw)
    if lowered in {"agents", "agentes"}:
        return Intent("agents", raw=raw)
    if lowered in {"setup", "configurar", "instalar", "wizard"}:
        return Intent("setup", raw=raw)
    if lowered in {"clear", "cls", "limpar"}:
        return Intent("clear", raw=raw)
    if lowered in {"chat", "conversar", "papo"}:
        return Intent("chat", raw=raw)
    if lowered in {"import", "importar", "memories", "memorias", "memórias"}:
        return Intent("import", raw=raw)

    # tier shortcut: ":gold", ":silver", ":bronze", ":auto"
    m = re.fullmatch(r":\s*(gold|silver|bronze|auto)", raw.lower().strip())
    if m:
        return Intent("use_tier", (m.group(1),), raw)
    m = re.fullmatch(r"use\s+(gold|silver|bronze|auto)", lowered)
    if m:
        return Intent("use_tier", (m.group(1),), raw)
    if lowered in {"continue", "continuar", "próximo", "proximo", "continuar do último erro", "continuar do ultimo erro"}:
        return Intent("continue", raw=raw)
    if lowered in {"plan", "plano"}:
        return Intent("plan", raw=raw)
    if lowered in {"delegate", "delegar"}:
        return Intent("delegate", raw=raw)

    m = re.fullmatch(rf"(?:run|rodar|executar)\s+{DELEGATION_ID}", lowered)
    if m:
        return Intent("run", (m.group(1),), raw)
    m = re.fullmatch(rf"(?:read|ler|summary|resumo|resumir(?:\s+o)?(?:\s+log)?(?:\s+do)?)\s+{DELEGATION_ID}", lowered)
    if m:
        return Intent("read", (m.group(1),), raw)
    m = re.fullmatch(rf"(?:log|abrir\s+log|mostrar\s+log)(?:\s+do|\s+de)?\s+{DELEGATION_ID}", lowered)
    if m:
        return Intent("log", (m.group(1),), raw)
    m = re.fullmatch(rf"(?:capsule|capsula|cápsula|abrir\s+capsule|abrir\s+capsula|abrir\s+cápsula)(?:\s+do|\s+de)?\s+{DELEGATION_ID}", lowered)
    if m:
        return Intent("capsule", (m.group(1),), raw)
    m = re.fullmatch(rf"(?:fix|corrigir|corrigir\s+o\s+erro\s+do|corrigir\s+erro\s+do)\s+{DELEGATION_ID}", lowered)
    if m:
        return Intent("fix", (m.group(1),), raw)
    m = re.fullmatch(r"compression\s+(safe|balanced|aggressive)", lowered)
    if m:
        return Intent("compression", (m.group(1),), raw)
    m = re.fullmatch(r"voice\s+(on|off|true|false|1|0)", lowered)
    if m:
        val = m.group(1) in {"on", "true", "1"}
        return Intent("voice", (val,), raw)

    if lowered.startswith("run "):
        return Intent("run_last", raw=raw)
    if lowered.startswith("delegate "):
        return Intent("objective", (raw.split(" ", 1)[1].strip(),), raw)
    if lowered.startswith("plan "):
        return Intent("plan_text", (raw.split(" ", 1)[1].strip(),), raw)

    return Intent("objective", (raw,), raw)
