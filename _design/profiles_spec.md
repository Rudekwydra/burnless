# Burnless Profiles — Spec v0.1

Status: DESIGN (2026-05-11)
Author: Opus 4.7 a pedido do Roberto
Target release: v0.7.4

## Problema

Roberto usa Burnless em múltiplos contextos no mesmo dia:
- Terminal **Claude Code** (Anthropic API ou subscription)
- Terminal **Antigravity** (Haiku como maestro, plugin chat)
- Terminal **Codex** (OpenAI)
- Terminal **Ollama** (local + Ollama Cloud, ex.: `ollama launch claude-desktop`)

Hoje a config é única (`~/.burnless/config.yaml`). Trocar entre contextos exige editar arquivo ou setar env vars na mão. Pain real e bloqueia uso natural multi-terminal.

## Solução

Profiles nomeados. Cada profile = config.yaml separado em diretório dedicado, selecionável por flag ou env var.

```
~/.burnless/
  profiles/
    claude.yaml      # Anthropic API direta
    claude-sub.yaml  # Claude Max/Pro subscription (OAuth)
    codex.yaml       # OpenAI / Codex CLI
    antigrav.yaml    # Haiku maestro via Antigravity
    ollama.yaml      # Ollama local + Cloud
  config.yaml        # default (fallback)
  state/             # cache state per-profile
    claude/
    codex/
    ollama/
```

## CLI

```bash
burnless --profile claude run "..."
burnless -p codex chat
burnless -p ollama delegate ...
BURNLESS_PROFILE=ollama burnless run ...    # env var equivalente
```

Sem `--profile`: usa `config.yaml` (compat retro).

## Auto-detect (opcional, opt-in)

`~/.burnless/profiles/_autodetect.yaml`:
```yaml
rules:
  - env: { TERM_PROGRAM: "Antigravity" }
    profile: antigrav
  - env: { CODEX_HOME: "*" }
    profile: codex
  - env: { OLLAMA_HOST: "*" }
    profile: ollama
  - env: { CLAUDECODE: "1" }
    profile: claude
  - default: claude
```

Auto-detect só ativa com `burnless --auto` ou `BURNLESS_AUTO=1`. Default é manual pra evitar surpresa.

## Schema de profile

Profile herda do `config.yaml` base e sobrescreve. Mantém DRY.

```yaml
# profiles/ollama.yaml
extends: ../config.yaml
brain:
  provider: ollama
  model: gpt-oss:120b
  endpoint: http://localhost:11434
workers:
  bronze:
    provider: ollama
    model: qwen2.5:7b
keepalive:
  enabled: false   # Ollama não tem cache prompt API; não vale
cache_policy:
  strategy: passthrough
```

```yaml
# profiles/claude-sub.yaml
extends: ../config.yaml
brain:
  provider: anthropic_oauth   # subscription via OAuth, não API key
  model: claude-opus-4-7
keepalive:
  enabled: true
  interval_sec: 270           # 4:30, abaixo dos 4:59 que evictam
cache_policy:
  strategy: roi_compact       # quando capsule recompact ficar pronto
```

## State isolation

Cada profile mantém estado de cache/sessão isolado em `~/.burnless/state/<profile>/`. Trocar profile não polui sessão atual. Lock por profile (`flock state/<profile>/.lock`) pra evitar 2 terminais do mesmo profile colidirem.

## Migração

`burnless profile init` cria estrutura de `profiles/` a partir do `config.yaml` atual, copiando como `profiles/default.yaml`. Roberto pode então duplicar/editar.

## Trabalho pra v0.7.4

1. `src/burnless/profiles.py` — loader, resolver, herança (~120 linhas)
2. `src/burnless/cli.py` — flag `--profile/-p`, env `BURNLESS_PROFILE` (~30 linhas)
3. `src/burnless/state.py` — isolation por profile (~40 linhas)
4. `burnless profile {list,init,show,switch}` — subcomandos (~80 linhas)
5. Testes — fixtures com 3 profiles distintos (~150 linhas)
6. Docs — seção em README + exemplo no `examples/profiles/`

Estimativa: 1-2 dias delegando bronze pra Codex/Sonnet, com Opus só auditando.

## Não-objetivos (v0.7.4)

- Auto-detect ligado por default (fica opt-in)
- UI desktop pra trocar profile (fica pro `_pro/desktop` futuro)
- Sync de profiles entre máquinas (pode virar feature do `_pro`)
