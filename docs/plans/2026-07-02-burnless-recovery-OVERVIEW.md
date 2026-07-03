# Burnless — Plano de Fix & Evolução (OVERVIEW)

**Data:** 2026-07-02 · **Auditor:** Fable (Cowork, auditoria profunda com 3 sub-auditorias paralelas)
**Escopo:** modo burnless nativo (Claude CLI + hooks), modo burnless pty (novo), auditoria de tokens, ergonomia LLM/MCP.
**Objetivo declarado do dono:** app funcional, profissional e limpo; fácil de auditar tokens; mínimo atrito para LLMs (RLHF).

Este overview indexa 3 planos executáveis. Cada item tem ID estável, evidência `arquivo:linha`, fix concreto e Verify. Execute em fatias pequenas, na ordem da seção "Ordem de execução". O protocolo de recuperação RM-4A…RM-4F é uma unidade arquitetural: pode ser dividido em commits, mas não deve ser parcialmente declarado pronto.

## Arquivos do plano

| Arquivo | Tema | Itens |
|---|---|---|
| `2026-07-02-P1-rolling-memory-native.md` | Modo nativo: rolling memory pós-/clear ("fio da meada") | RM-1 … RM-16 |
| `2026-07-02-P2-pty-mode.md` | Pilot PTY host-neutral: Claude/Codex nativos sob adapters | PTY-0 … PTY-9 |
| `2026-07-02-P3-tokens-llm-ergonomics.md` | Ledger de tokens honesto + superfície MCP/CLI para LLMs | TK-1 … TK-15, MCP-1 … MCP-9 |

## Diagnóstico executivo (por que o fio da meada quebra hoje)

Causas-raiz ranqueadas, todas comprovadas no código e no estado em disco deste repo:

1. **O checkpoint recente está VAZIO.** O rewriter local (ollama/gemma, timeout 20s hardcoded) falha e o fail-open de `apply_capture` grava `living.md` de 0 bytes sem alarme. Comprovado: 7 de 15 `living.md` em `.burnless/epochs/` têm 0 bytes — incluindo os 3 chats mais recentes; o `_rolling/seed.md` de hoje tem 42 bytes (só o header). O resume então serve memória de horas/dias atrás. → **RM-1, RM-2**
2. **A última troca nunca chega a tempo.** O Stop inteiro está registrado como async e o `ring/` só é escrito depois da chamada LLM de 20–120s. Enquanto isso, `/clear` dispara o resume em ~1s. Esperar lock não resolve; falta um journal write-ahead e um watermark que prove o que entrou no checkpoint. → **RM-4A…RM-4F**
3. **Bifurcação V1/V2 por env divergente.** O Stop hook captura em V1 (sem `BURNLESS_EPOCH_V2`); o SessionStart resume em V2 (com a var). No resume, qualquer `living.md` não-vazio **eclipsa** todas as chains V1 — sessões capturadas em V1 ficam invisíveis para sempre. → **RM-3**
4. **O owner-loop nunca serve cache.** O fingerprint do refine (exclui o chat corrente) nunca bate com o do resume (inclui). Telemetria: 100% `served: floor, cache_hit: false`. → **RM-5**
5. **Conteúdo pobre na origem.** Só o último par textual, às vezes invertido por task-notifications, sem nenhum tool_use/arquivo em curso. → **RM-6**
6. **O modo `/burnless on` reseta no /clear** (mode file por session-id). → **RM-9**

No lado do produto: a superfície MCP diverge do CLI exatamente onde os docs prometem equivalência (`run(background)` quebrado desde sempre, `delegate` sem validações, tool `do` ausente), e o headline de tokens soma estimativas em dobro precificadas a opus, enquanto o usage REAL que o sistema já captura morre sem persistência.

## Ordem de execução (fases)

**Fase 0A — Criar a fonte de verdade sem LLM (P1)**
RM-6 (extractor estruturado) → RM-4A (journal síncrono/idempotente) → testes de append concorrente e dedupe. Enquanto esta fase não passar, não automatizar `/clear` nem PTY.

**Fase 0B — Checkpoint transacional e anti-delay (P1)**
RM-1 → RM-2 → RM-3 → RM-4B (checkpoint/watermark) → RM-4D/E (restore checkpoint+delta/budget) → RM-14 (compatibilidade legacy).

**Fase 0C — Handoff real do `/clear` (P1)**
RM-4C (SessionEnd clear) → RM-11 (um único restore pipeline) → RM-4F (observabilidade) → `burnless init --claude-code --force`. Gate: rewriter dormindo 120s + `/clear` imediato precisa restaurar em <1s com a última troca exatamente uma vez.

**Fase 1 — Fio da meada de verdade (P1)**
RM-9 → RM-5 → RM-15. Ao fim desta fase, o teste de aceitação é o cenário real: trabalhar 5+ turnos, dar `/clear`, e a sessão nova saber (a) objetivo corrente, (b) últimos arquivos tocados, (c) próximo passo.

**Fase 2 — Confiança nos números (P3 parte A)**
TK-13 (`metrics --explain`) → TK-1 → TK-2 → TK-3 → TK-7 (usage real persistido) → TK-5 (unificar 4 comandos). O resto de TK-* depois.

**Fase 3 — MCP sem atrito (P3 parte B)**
MCP-1 → MCP-2 → MCP-3 → MCP-4 → MCP-5 → MCP-9 (docs). 

**Fase 4 — Modo pty (P2)**
Só depois das fases 0A–0C e 1. PTY-0 (contrato host-neutral + fake host) → PTY-1 (relay) → adapter Claude → adapter Codex → PTY-2…PTY-5. Nenhum milestone avança sem seus testes automatizados; smoke real dos dois CLIs é gate de release.

**Fase 5 — Limpeza profissional (espalhado)**
RM-7, RM-8, RM-10, RM-12, RM-13, RM-16, PTY-6…PTY-9, TK-restantes, MCP-restantes.

## Protocolo para o executor (Sonnet)

1. Leia o arquivo P do item antes de tocar código; a evidência `arquivo:linha` foi verificada em 2026-07-02 — se o código mudou, re-localize antes de editar.
2. Um item ou subitem por commit. Mensagem: `fix(P1/RM-4A): ...`. Não marcar Fase 0 completa enquanto 0A, 0B e 0C não passarem juntas.
3. Todo item tem bloco **Verify** — rode-o. Rode também `.venv/bin/python -m pytest tests/ -x -q` (baseline 2026-07-02: 912 passed; 3 falhas pré-existentes em `test_ollama_worker.py` são conhecidas e não relacionadas).
4. **Não repita** os 12 fixes do Codex de 2026-07-02 (`docs/audits/2026-07-02-codex-deep-audit-RESULTADO.md`) — já aplicados.
5. Cada fix comportamental ganha teste comportamental (não teste de "string presente no source" — anti-padrão já apontado em auditoria).
6. Ao concluir uma fase, atualize a tabela de status no fim deste arquivo.
7. Não use ausência de lock, PID, `mtime` ou `sleep` como prova de consolidação. A única prova é `checkpoint.applied_through` comparado ao `journal_head`.
8. Preserve mudanças preexistentes no worktree. Antes de publicar/commitar, rode `scripts/public_git_check.sh` e `.venv/bin/python -m pytest`.
9. Implemente teste junto com comportamento. Para PTY/provider, comece pelo adapter contract e fake host; não use o CLI real como único teste e não deixe a suíte para o final.

## Status

| Fase | Status | Data |
|---|---|---|
| 0A | concluída | 2026-07-02 |
| 0B | concluída | 2026-07-02 |
| 0C | concluída | 2026-07-02 |
| 1 | concluída | 2026-07-02 |
| 2 | pendente | |
| 3 | pendente | |
| 4 | pendente | |
| 5 | pendente | |
