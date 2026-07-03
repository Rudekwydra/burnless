# Auditoria final — native clear (P1) + pilot (P2)

**Data:** 2026-07-03 · **Auditor:** Fable (Cowork)
**Baseline de testes:** 964 passed (suíte completa, sandbox Linux; exclusões: `test_ollama_worker` — 3 falhas conhecidas; `test_book_*` — sys.path de `book/`).

## Fixes aplicados nesta sessão (worktree, não commitados)

### FX-1 · CRÍTICO — `claim_handoff` nunca casava no `/clear` real
O Claude Code não envia `process_instance_id` em nenhum payload de hook. O fallback era `session_id`: o SessionEnd gravava o handoff com o SID **antigo** e o SessionStart tentava o claim com o SID **novo** → match exato falhava → restore vazio, sempre. Os testes passavam porque injetavam `"proc-1"` nos dois lados.
- `recovery.claim_handoff`: fallback RM-4C.4 — sem match por PID, aceita o handoff não-reclamado mais fresco do mesmo host/projeto dentro de TTL (120s, `HANDOFF_CLAIM_TTL_SECONDS`); `claim_mode: pid|ttl_fallback` no payload e no evento.
- Scripts (stop/end/session): `host_pid()` — PID do ancestral `claude*|node*` como lineage estável (sobrevive ao `/clear`, distingue janelas); fallback SID.
- Testes: `test_claim_handoff_ttl_fallback_*`, `test_claim_handoff_prefers_pid_match_over_fresh_foreign`, `test_epoch_session_hook_restores_with_realistic_claude_payload` (payload real, sem `process_instance_id`).

### FX-2 · CRÍTICO — SessionEnd envia `reason`, não `source`
`burnless_epoch_end.sh` lia só `source` → em produção saía sem journalizar nem gravar handoff. Agora aceita `source or reason`. Teste: `test_epoch_end_hook_accepts_reason_clear`.

### FX-3 · CRÍTICO — parser do `extract-exchange` sem `--transcript`/`--cwd`
Os hooks chamam `epoch extract-exchange --transcript ... --cwd ...`, mas o subparser (`epoch_core`) não tinha esses args → argparse rc=2, silenciado por `2>/dev/null` → **o Stop hook nunca journalizava nada**. Args adicionados ao `epoch_core`.

> Os três bugs acima formavam uma cadeia: nada entrava no journal (FX-3), o handoff não era escrito (FX-2) e, se fosse, não seria reclamado (FX-1). O e2e "de verdade" (claude real + /clear) nunca tinha sido exercitado — os testes chamavam as funções Python direto.

### FX-4 · RM-2 (estava pendente) — rewriter config-driven
`living_rewriter` usava ollama `:11434` com timeout **20s** hardcoded (causa-raiz nº 1 do plano; consolidação fadada a falhar com modelo local frio). Agora: `encoder.endpoint`, `encoder.timeout_s` (defaults 11434/**90s**), `encoder.local_api`; `BURNLESS_LOCAL_API` vira override. Testes: `tests/test_living_rewriter_config.py`. **Falta:** documentar em `docs/COMMANDS.md`.

### FX-5 · Pilot — `claude -C` não existe
`-C` é flag do codex; o spawn do claude falharia. Removido do `ClaudeAdapter` (o pilot já seta `cwd` no spawn). Teste atualizado.

## Verificação do que o executor marcou como concluído (P1, fases 0A–1)

Corretos e bem-feitos: `recovery.py` (journal append-only + lock curto + dedupe por identidade de records; checkpoint atômico tmp+fsync+replace com watermark; fail-open que preserva checkpoint; restore checkpoint+delta sem LLM; budget determinístico; observabilidade completa em `owner_loop.jsonl`); hooks síncronos no append; SessionEnd wired no init/doctor/unwire; RM-3 (env fora dos templates), RM-6 (par ordenado + sidechain + files), RM-7, RM-8, RM-9 (fallback por projeto + TTL + GC), RM-16 (sanitização).

## Pendências P1 (em ordem de prioridade)

1. **RM-4B parcial — compactador sem lease nem revalidação de geração.** `compact_pending` lê checkpoint → LLM → grava, sem lease por chat e sem revalidar `generation/applied_through` após o LLM. Stop e End disparam compact em background a cada turno ⇒ dois compactadores concorrentes fazem last-writer-wins (checkpoint mais novo pode ser sobrescrito por resultado stale). Fix: lease com TTL baseado no timeout do encoder + re-lock e revalidação antes do commit; descartar resultado stale.
2. **Startup sem memória.** `burnless_epoch_session.sh` só injeta em `source=clear`; `burnless_session_seed.sh` só serve `pending_seed.md` (escrito apenas pelo `restart_rollover.sh`). Um `claude` novo no dia seguinte não recebe nada. Sugestão: em `source=startup` sem handoff fresco, servir o checkpoint mais recente do projeto (mesmo renderer, budget menor).
3. **Owner-loop órfão + comentário-fantasma.** Nenhum hook chama mais `refine-owner`; o comentário "legacy compatibility: epoch refine-owner" no stop hook existe só para passar o grep de `test_epoch_stop_script_has_refine_owner_call` — exatamente o anti-padrão que o protocolo do plano proíbe. Decidir: religar refine pós-compaction (fingerprint RM-5 no mesmo conjunto) ou aposentar o caminho e apagar teste+comentário.
4. **RM-3 residual.** `BURNLESS_EPOCH_V2` ainda gateia `epochs.py:469/690` e `cli.py:1318`; sem a var nos templates esses branches V2 estão mortos por default. Migrar o gate para config (`epochs.version`) ou remover o código morto.
5. **RM-1 residual.** O caminho legacy `epoch capture`/`apply_capture` ainda cria `living.md` vazio em falha e sem log. Se ficou sem caller, deprecar/remover para o bug não renascer.
6. **RM-12 residual.** Tudo `2>/dev/null`; sem `hook_errors.log` nem report no doctor — foi isso que escondeu FX-2/FX-3. Vale priorizar.
7. **RM-4F parcial.** `burnless session` não mostra `checkpoint gen / applied/head / pending / último erro`.
8. **Sem GC/retention do journal** (RM-4B.6) e truncamento do restore sem referência ao JSON completo (RM-4E.2).
9. **Golden harness (`must_remember`/`must_forget`) e bench p50/p95 não implementados** (Verify 10–11 do RM-4).
10. **Testes com paths absolutos `/Users/roberto/...`** (`test_owner_loop_async.py:205`, `test_epochs_v3_wiring.py:56`, `test_contextgc.py`) — quebram em CI/outra máquina; usar `Path(__file__).parents[1]`.
11. `epochs.version` default ainda 2 (RM-15 pedia 3 como default ou no config do projeto — confirmar se o config real já carrega `version: 3`).

## Estado do pilot (P2) e a última etapa

Feito: PTY-1/M0 relay + testes; seleção/persistência de host; doctor/report; sidecar `events.py` + `summarize_run_events` (idle conservador); `rollover.py` (avaliar/armar/preparar/monitor); fake host; ciclo respawn no `cmd_pilot`.

O que falta para o pilot funcionar fim-a-fim (nesta ordem):

1. **Produtor de `PilotEvent`.** Ninguém escreve `.burnless/pilot/runs/<run_id>/events.jsonl` — hooks não conhecem `BURNLESS_PILOT_RUN_ID`. Sem eventos, `idle=False` sempre ⇒ rollover permanentemente bloqueado. Fix: hooks (stop/end/session) anexam evento ao sidecar quando `BURNLESS_PILOT_RUN_ID` estiver no env (o pilot já o exporta ao filho).
2. **`context_usage` real.** Ambos adapters retornam `unknown` ⇒ segundo bloqueio permanente. Claude: somar usage dos records assistant do transcript da sessão (localizar por SID via events). Codex: rollout/status. Manter `unknown` ⇒ nunca inventa número (contrato já testado).
3. **Entrega do restore ao respawn.** `prepare_rollover` renderiza o payload e ninguém injeta na sessão nova — a sessão respawnada nasce em branco. Bridge claude: escrever `~/.burnless/state/pending_seed.md` (consumo em startup já existe no seed script). Codex: prompt inicial no argv ou hook session-start próprio.
4. `locate_session`/`is_turn_idle` reais no adapter claude (via events + transcript), e só então considerar PTY-5 (`native-clear` — hoje o `ClaudeAdapter` declara `reset_strategy="native-clear"` sem implementação; alinhar metadata para `respawn` até lá).
5. PTY-6..9 (limpeza: `maestro_adapters` morto, `keepalive.py`, `session_holder`, leftovers do corte de chat).

## Sugestões novas (gerais)

- **Doctor: alarme de watermark.** Reportar `applied_through << journal_head` persistente por sessão — detector barato de "consolidação quebrada" que teria pegado RM-2/FX-3 em produção.
- **Smoke real como gate.** Um script `scripts/smoke_native_clear.sh` (claude headless + `/clear` + assert do restore) rodado antes de release — os três FX críticos eram invisíveis a testes unitários por construção.
- **`claim_mode` no doctor/session** para o usuário ver quando a lineage caiu no fallback TTL (garantia menor, RM-4C.5).
- **TTL do fallback configurável** (`epochs.handoff_claim_ttl_s`) para quem usa múltiplas janelas intensamente.
- Rodar `burnless init --claude-code --force` após commitar (os scripts instalados em `~/.claude/scripts/` divergem dos templates corrigidos) e conferir `doctor | grep -i drift`.
