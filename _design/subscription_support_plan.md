# Burnless Subscription Plan Support — Attack Plan

Status: PLANNING (2026-05-11)
Goal: Burnless v0.7.4 funcional pra **Claude Max/Pro subscription** (público maior + alvo pro tier).

## Diagnóstico (do Explore agent, 2026-05-11)

| Componente | Status | Local |
|---|---|---|
| Keepalive 4:59min — CLI | ✅ Implementado, só ativa com `ANTHROPIC_API_KEY` | `src/burnless/keepalive.py` |
| Keepalive — Desktop Tauri | ❌ Não wired | `_pro/desktop/src-tauri/src/main.rs` |
| Capsule recompaction ROI | ❌ Designed (`MATH.md §4`), não built | falta `src/burnless/cache_policy.py` |
| Warm cache telemetry | ❌ Spec 251 linhas, zero código | falta `src/burnless/telemetry.py` |
| Subscription tier detection (Max vs Pro) | ⚠️ Headers OAuth lidos, gating não feito | `_pro/desktop/src-tauri/src/main.rs` |
| Subscription mode na CLI | ❌ keepalive bloqueia se não tem `ANTHROPIC_API_KEY` | `keepalive.py:21-26` |

## Fases tickáveis

### Fase 1 — Keepalive funciona em subscription (CLI + Desktop)  [ALVO IMEDIATO]
**Por que primeiro:** sem isso, usuário de subscription perde cache a cada 4:59min e Burnless deixa de ter valor pro segmento maior.

- [ ] **1.1** Detectar subscription via OAuth no CLI (`keepalive.py`). Hoje exige `ANTHROPIC_API_KEY`; precisa aceitar Claude OAuth token também. Se OAuth detectado → ativar keepalive com modo "subscription" (usa endpoint OAuth, não API key).
- [ ] **1.2** Endpoint de ping em modo OAuth: validar se OAuth aceita request mínimo de 1 token (testar). Se não, usar approach alternativo: ler `anthropic-ratelimit-unified-5h-utilization` periodicamente como touch.
- [ ] **1.3** Spawn keepalive daemon no Tauri Desktop (`_pro/desktop/src-tauri/src/main.rs`). Subprocess Python OR portar lógica pra Rust (preferir subprocess Python pra reuso).
- [ ] **1.4** UI no desktop pra mostrar status keepalive (ON/OFF, último ping, próximo ping).
- [ ] **1.5** Testes: 5h+ rodando, medir cache_read_input_tokens não-zero em todas as turns.

**Estimativa:** 5-7 dias delegando.

### Fase 2 — Capsule recompaction ROI
- [ ] **2.1** Criar `src/burnless/cache_policy.py` com função `should_recompact(state)` baseada em `MATH.md §4`.
- [ ] **2.2** Integrar em `cached_worker.py` pós-turn.
- [ ] **2.3** Flag `--recompact=auto|never|always`.
- [ ] **2.4** Testes com fixtures de N turns simulados.

**Estimativa:** 3-5 dias.

### Fase 3 — Warm cache telemetry (opt-in)
- [ ] **3.1** `src/burnless/telemetry.py` com eventos `warm_hit`/`cold_miss`/`idle_gap`/`parallelism_loss`.
- [ ] **3.2** Batching HTTP + endpoint stub (Supabase edge).
- [ ] **3.3** Flag opt-in `telemetry: true` no profile/config.
- [ ] **3.4** Privacy review (PII guard).

**Estimativa:** 3 dias.

### Fase 4 — Subscription tier gating (Max vs Pro)
- [ ] **4.1** Parser de headers OAuth → infer plan tier.
- [ ] **4.2** Gate features Pro-only baseado em tier (encoder full-LLM vs telegrafista, Active Capsule Routing).
- [ ] **4.3** UI desktop mostra plano detectado.

**Estimativa:** 2 dias.

## Workflow

- Fase 1.1, 1.2 — Codex bronze investiga API OAuth real e propõe patch
- Fase 1.3 — Codex/Sonnet implementa Rust+Python wiring
- Fase 1.4 — Sonnet faz UI Tauri
- Fase 1.5 — Roberto valida com sessão real de 5h+
- Opus audita PR final

## Definition of Done — v0.7.4

1. Usuário com Claude Max OAuth (sem ANTHROPIC_API_KEY) roda `burnless chat` e vê cache_read_input_tokens > 0 em turns > 5min após start.
2. Desktop Tauri spawna keepalive automático ao abrir.
3. CHANGELOG documenta subscription support como first-class.
4. README seção "Subscription users" com setup.
5. PyPI + GitHub release tagged.
