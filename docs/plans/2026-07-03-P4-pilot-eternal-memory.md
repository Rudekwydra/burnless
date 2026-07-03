# P4 — Pilot autônomo + memória eterna provada

**Data:** 2026-07-03 · **Autor:** Fable (Cowork) · **Executor:** Sonnet/Codex
**Pré-requisito:** commit `b420d9b` + herança de checkpoint (já no worktree: `recovery.inherit_checkpoint`, `epoch inherit`, wiring em `epoch restore` e no branch pending_seed do seed script; testes em `test_epoch_recovery_core.py`).

**Protocolo:** mesmo do OVERVIEW de 2026-07-02 — um item por commit (`fix(P4/EM-x): ...`), teste comportamental junto, evidência re-localizada antes de editar, `public_git_check.sh` + pytest antes de publicar. Lição das auditorias: teste que injeta payload sintético "bonito" não conta como prova — todo item aqui tem um Verify com o formato REAL do host.

## Contexto do que já está feito (não repetir)

Journal/checkpoint/handoff/restore/lease/inheritance completos no core. Pilot: relay PTY, seleção de host, eventos sidecar produzidos pelos hooks Claude (`pilot-event` gated por `BURNLESS_PILOT_RUN_ID`), `context_usage` claude (`estimated` via usage_meter) e codex (`exact` via rollout `token_count`), rollover respawn com bridge `pending_seed.md`, HUD/doctor com watermarks.

---

## EM-1 · CRÍTICO — Codex não journaliza: pilot codex é relay sem memória

**Evidência:** `grep -rln codex templates/scripts/` = vazio; nenhum hook codex instala `journal-append`. Sem journal ⇒ `write_handoff` com `journal_head=0` e `render_restore` retorna None ⇒ `prepare_rollover` fica `not_ready` para sempre no host codex.

**Fix:**
1. `burnless init --codex` (ou parte do `pilot doctor --fix`): instalar hooks codex (`UserPromptSubmit`/`Stop`/`SessionStart` — confirmar nomes no codex 0.135+ via capability probe) em `~/.codex/config.toml`, espelhando os scripts claude: extract do rollout (`~/.codex/sessions/**/*.jsonl`, records `response_item`/`turn.completed`) → `journal-append --host codex`.
2. Se o codex instalado não suportar algum hook: fallback watcher no pilot — tail do rollout da sessão ativa (o adapter já sabe localizar por cwd em `logs.py`) convertendo turnos completos em envelopes `host=codex`. O core já é host-neutral; só o produtor muda.
3. `extract-exchange` ganha `--format codex-rollout` (parser testável, mesmo contrato do claude transcript: par ordenado, filtro de noise/restore, files de tool_use).

**Verify:** fixture com rollout REAL do codex (copiar um `~/.codex/sessions/*.jsonl` sanitizado para `tests/fixtures/`); teste roda extract+journal e afirma par correto + dedupe; teste de `prepare_rollover` com `host=codex` chegando a `status: ready`. Smoke opt-in: `burnless pilot --host codex`, 2 turnos, rollover manual, restore contém a última troca.

## EM-2 · IMPORTANTE — Usage do Claude: de `estimated` (heurística) para `exact` (transcript)

**Evidência:** `pilot/logs.py:claude_context_usage` usa `claude_usage_delta.cold_baseline_input_tokens` + limite chutado 200k. Serve para disparar, mas erra o momento (rollover cedo/tarde) e o HUD mente.

**Fix:** localizar o transcript da sessão pilotada (o sidecar `events.jsonl` já tem `transcript_path` dos hooks) e ler o usage do ÚLTIMO record assistant (`message.usage.input_tokens + cache_read_input_tokens + cache_creation_input_tokens`); `model` → limite real por tabela (fallback 200k, confiança `estimated`). Retornar `exact` quando vier do transcript.

**Verify:** fixture de transcript real com 3 turnos e usage crescente ⇒ `context_usage` retorna o valor do último assistant, `confidence=exact`; payload sem usage ⇒ `unknown` (nunca inventa — teste já existe, manter).

## EM-3 · IMPORTANTE — Auto-rollover default ON + `--no-auto`

**Evidência:** `cli.py:2655` — auto só com flag/config. O plano PTY-3 especifica `--no-auto` (auto é o default do produto).

**Fix:** default `pilot.auto_rollover: true` quando o host tem capability completa (events + usage com confiança aceita); `--no-auto` desliga; se capabilities faltarem, auto desarma sozinho com diagnóstico no HUD/report (nunca silencioso). Documentar em COMMANDS.md.

**Verify:** teste: fake host com capabilities completas ⇒ monitor inicia sem flag; sem usage ⇒ monitor não inicia e `pilot --report` mostra o motivo.

## EM-4 · IMPORTANTE — PTY-5: reset por `/clear` nativo (sem matar a TUI)

**Evidência:** rollover atual = SIGTERM no process group + respawn (TUI morre e renasce). `ClaudeAdapter.capabilities()` já declara `reset_strategy="native-clear"` sem implementação — **alinhar a metadata para `respawn` no commit inicial deste item** e só devolver `native-clear` quando funcionar.

**Fix (ordem):**
1. Idle provado: só com evento `stop` fresco no sidecar E nenhum `user_prompt` posterior (o produtor UserPromptSubmit já existe? se não, adicionar `pilot-event --event user_prompt` ao mode hook).
2. Com idle provado, escrever `/clear\n` no stdin do PTY (o relay é dono do fd); o fluxo SessionEnd(clear)→handoff→SessionStart(clear)→restore+inherit JÁ funciona no modo nativo — o pilot só injeta o comando.
3. Guard: se em 5s não vier `session_start` no sidecar, abortar para respawn (fallback preservado).
4. `pilot.rollover_mode: native-clear|respawn` por config; capability probe decide o default por host (codex: `/new`).

**Verify:** fake host que ecoa `/clear` e emite `session_end/session_start` ⇒ ciclo completo sem respawn; timeout do guard ⇒ respawn. Smoke real opt-in com `rollover_at_tokens` baixo: sessão continua na MESMA TUI, restore presente, `claim_mode=pid`.

## EM-5 · IMPORTANTE — Golden harness: prova da memória eterna

**Evidência:** `grep must_remember tests/ src/` = vazio (Verify 10 do RM-4 nunca implementado). Sem isso, "memória eterna" é claim, não fato — e a herança de checkpoint (recém-implementada) é exatamente o que ele deve medir.

**Fix:** `tests/test_memory_golden.py` + `tests/fixtures/golden/*.yaml`: cenários de 20+ turnos com 3 rollovers (journal sintético + rewriter determinístico fake), cada um declarando `must_remember` (objetivo, decisões vigentes, arquivos, próximo passo) e `must_forget` (decisões substituídas, threads encerradas). Roda o pipeline real: journal → compact → handoff → claim → inherit → restore → repete. Assert: todas as chaves `must_remember` presentes no restore do 3º rollover; nenhuma `must_forget`; última troca pendente literal; payload dentro do budget.

**Verify:** o próprio harness em CI. Bônus: um cenário com rewriter que falha nos rollovers 2–3 (degradação checkpoint+delta) — `must_remember` do rollover 1 ainda presente via herança.

## EM-6 · MENOR — Smoke real versionado (gate de release)

`scripts/smoke_native_clear.sh`: init --force num tmpdir-project, `claude -p`/headless com 3 turnos, `/clear` simulado via SessionEnd+SessionStart payloads REAIS (capturados de uma sessão de verdade e commitados como fixture), assert restore <1s com última troca exatamente uma vez + `claim_mode`. `scripts/smoke_pilot.sh` idem para os dois hosts (opt-in, exige CLIs instalados). Documentar como gate em RELEASE.md.

## EM-7 · MENOR — Higiene pós-P4

- `pilot --report`: linha `inheritance: <sid herdado> gen N` (o evento `checkpoint_inherited` já existe no owner_loop).
- GC de handoffs claimed antigos (>7d) junto do prune de journal.
- COMMANDS.md: `epoch inherit`, `pilot.rollover_mode`, hooks codex.

## Ordem de execução

EM-2 → EM-3 → EM-1 → EM-5 → EM-4 → EM-6 → EM-7.
(EM-2/3 destravam o auto no claude já; EM-1 traz o codex; EM-5 antes do EM-4 para o native-clear nascer medido.)

## Aceitação da fase

1. `burnless pilot` (sem flags) num projeto claude: trabalha até o limiar, rollover automático invisível (native-clear) ou respawn com seed, HUD mostra `↻ 143k → checkpoint 3.2k + N pending`, e o documento vivo do rollover 3 ainda contém o objetivo do turno 1.
2. Mesmo cenário com `--host codex`.
3. Golden harness verde em CI; smokes verdes localmente antes do release.
