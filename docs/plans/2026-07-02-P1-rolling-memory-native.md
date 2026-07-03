# P1 — Modo nativo: rolling memory pós-/clear

**Contexto:** o modo nativo funciona via hooks do Claude Code: `UserPromptSubmit` (`templates/scripts/burnless_mode_hook.sh`), `SessionStart` (`burnless_session_seed.sh` + `burnless_epoch_session.sh`) e `Stop` (`burnless_epoch_stop.sh`). O `/clear` encerra a sessão antiga com `SessionEnd(reason:"clear")`, cria uma sessão nova e dispara `SessionStart(source:"clear")` ([contrato oficial de hooks](https://code.claude.com/docs/en/hooks)). Hoje Burnless não usa o `SessionEnd`, não mantém um handoff explícito entre os SIDs e procura predecessores por `mtime`. O mecanismo existe; ele quebra pelos itens abaixo.

**Fluxo real (referência):**
```
Stop ─► hook inteiro marcado async ─► extrai último par u/a ─► rewriter LLM lento
     └─ somente DEPOIS grava living.md/state.json/ring e atualiza seed
/clear ─► SessionStart(NOVO_SID) ─► procura predecessor por mtime
        └─ pode restaurar checkpoint anterior sem a última troca
```

**Estado em disco (inventário atual):** `.burnless/epochs/<sid>/{NNN.md,living.md,state.json,ring/NNN.md,originais/}`, `.burnless/epochs/_rolling/{seed.md,refined_seed.json}`, `.burnless/epochs.off` (opt-out real), `.burnless/owner_loop.jsonl`; globais: `~/.burnless/state/{pending_seed.md,session-<sid>.mode,session-<sid>.rollover.md(morto)}`, `~/.claude/settings.json` + `~/.claude/scripts/burnless_*.sh`.

### Invariantes obrigatórios da recuperação

1. **Durable-before-slow:** a troca original precisa estar persistida antes de qualquer chamada LLM e antes do hook retornar.
2. **Checkpoint explícito:** uma troca está consolidada somente se seu `seq` estiver coberto por `applied_through` no checkpoint canônico; PID, ausência de lock e `mtime` não provam consolidação.
3. **Restore sem espera:** `SessionStart(source=clear)` injeta o último checkpoint mais as trocas do journal com `seq > applied_through`. Nunca aguarda o rewriter.
4. **Exactly-once no payload:** uma troca aparece no restore como parte do checkpoint ou como delta original, nunca nos dois caminhos por inferência temporal.
5. **Fail-open sem perda:** timeout, crash ou output inválido do rewriter preserva checkpoint e journal; o sistema degrada para `checkpoint + delta`.
6. **Um restore pipeline:** um renderer compartilhado monta o bloco pós-`/clear`; cada host tem no máximo um entrypoint SessionStart. No Claude, `burnless_epoch_session.sh` chama o renderer e `burnless_session_seed.sh` fica restrito ao respawn/startup explícito.
7. **Core host-neutral:** journal, checkpoint, watermark e restore não conhecem paths ou payloads específicos de Claude/Codex. Adapters convertem eventos do host para o envelope Burnless.

### Estado alvo mínimo

```text
.burnless/epochs/sessions/<host>/<host_session_id>/
  journal/000001-<exchange_id>.json   # append-only, original local
  checkpoint.json                    # canônico e atomicamente substituído
  living.md                          # mirror compatível, não fonte de commit
  state.json                         # mirror compatível
  ring/                              # compatibilidade/retention; não é commit log
.burnless/epochs/_rolling/
  handoffs/<old_sid>.json            # SessionEnd(clear) -> SessionStart(clear)
  refined_seed.json
```

`checkpoint.json` contém no mínimo `schema`, `generation`, `host`, `host_session_id`, `process_instance_id`, `living_md`, `harvested_state`, `applied_through`, `updated_at` e `content_hash`. Escrita: tmp + `fsync` + `os.replace`. Diretórios legacy `.burnless/epochs/<sid>/` continuam legíveis como `host=claude`; não exige migração destrutiva.

---

## RM-1 · CRÍTICO — Fail-open grava `living.md` VAZIO e destrói o checkpoint

**Evidência:** `epochs_v2.py:551-556` (rewriter retorna None/vazio ⇒ grava `living.md` vazio se não existir e segue) e `:589-595` (except genérico ⇒ idem). `carry_forward_chain` exclui docs vazios (`epochs.py:457`) ⇒ a sessão contribui com NADA. **Comprovado em disco:** 7/15 `living.md` com 0 bytes, incluindo os 3 chats mais recentes (`dcf60b96` de hoje 06:23, `0733f651`, `4d412c57`); `ring/` desses chats tem trocas reais (a captura rodou, o rewriter falhou); `_rolling/seed.md` de hoje = 42 bytes (só o header `> ordem: documento vivo`).

**Fix:**
1. Em falha do rewriter, NUNCA gravar/zerar `living.md`: manter o anterior intacto.
2. Fallback em cascata: (a) tentar `epoch_summarizer` V1 somente se cloud fallback estiver explicitamente configurado; (b) senão, deixar a troca pendente no journal do RM-4 para a próxima consolidação. Não criar um segundo diretório `pending/`.
3. Logar a falha em `owner_loop.jsonl` (`{"phase":"capture","error":...}`) em vez de silêncio.

**Verify:**
```sh
cd /Users/roberto/antigravity/burnless && python -m pytest tests/ -q -k "epochs_v2 or living" 
# teste novo: rewriter que retorna None NÃO zera living.md pré-existente com conteúdo
grep -q "def test_capture_rewriter_failure_preserves_living" tests/test_epochs_v2*.py
```

## RM-2 · CRÍTICO — Timeout/endpoint do rewriter hardcoded e irreal

**Evidência:** `epochs_v2.py:617-625` — ollama `localhost:11434` com **timeout 20s** hardcoded (irreal para prompt ~2.5k tokens em modelo local frio); llamacpp `:11435`/120s só se `BURNLESS_LOCAL_API=llamacpp` estiver no env — **nenhum hook seta isso**. Config real do projeto: `encoder: ollama-local + gemma`.

**Fix:** ler endpoint/timeout do `config.yaml` (`encoder.endpoint`, `encoder.timeout_s`, defaults 11434/90s); `BURNLESS_LOCAL_API` vira override, não a única via. Documentar em `docs/COMMANDS.md`.

**Verify:** teste que injeta `encoder.timeout_s: 7` no config e afirma que o `urlopen` recebe `timeout=7` (monkeypatch em `urllib.request.urlopen`).

## RM-3 · CRÍTICO — Bifurcação V1/V2 por env divergente + "V2 eclipsa V1"

**Evidência:** `burnless_epoch_session.sh:7` exporta `BURNLESS_EPOCH_V2=1`; o template `burnless_epoch_stop.sh:48` chama `epoch capture` **sem** a var (só o `refine-owner` da linha 50 tem) ⇒ captura V1, resume V2. Gate em `cli.py:1252-1286`. Em `carry_forward_chain`, qualquer `living.md` não-vazio faz o branch V2 retornar e as chains V1 **nunca** são consideradas (`epochs.py:448-607`). Disco confirma estado misto (dirs com `NNN.md` e dirs com `living.md`). Adendo: 0 eventos `"phase":"refine"` em `owner_loop.jsonl` ⇒ o hook INSTALADO em `~/.claude/scripts/` diverge do template do repo.

**Fix:**
1. Matar o gate por env: decidir V1×V2 por `config.yaml` (`epochs.version`, já lido por `_epochs_version`, `epochs_v2.py:498`) nos DOIS lados (capture e resume).
2. No resume, merge V2+V1 por mtime em vez de eclipse (chains V1 de sessões recentes entram no floor).
3. Rodar `burnless init --claude-code --force` e adicionar check de drift template×instalado no `doctor` (byte-compare já existe em `init_claude_code.py:94-106`; falta reportar/fixar no doctor).

**Verify:**
```sh
grep -c 'BURNLESS_EPOCH_V2' templates/scripts/*.sh   # esperado: 0 após o fix
python -m pytest tests/ -q -k "carry_forward or epoch_resume"
burnless doctor | grep -i drift
```

## RM-4 · CRÍTICO — Recovery protocol: journal write-ahead + checkpoint watermark + handoff de `/clear`

**Evidência:** `wire_settings_hook` registra o Stop inteiro com `"async": true` (`init_claude_code.py:168`), e `burnless_epoch_stop.sh:46-51` ainda cria outro background interno. Em `apply_capture`, a chamada lenta ao rewriter ocorre em `epochs_v2.py:549`, mas `push_ring` só roda em `:555` ou `:585`. Portanto, no intervalo exato em que `/clear` passa à frente, nem `living.md` nem `ring/` contêm a última troca. Dois captures também podem ler o mesmo `prev_md` e fazer last-writer-wins. Esperar lock ou testar sua ausência não resolve: lock não prova quais trocas entraram no checkpoint.

### RM-4A — Extração e append síncronos

1. Implementar `burnless epoch extract-exchange --transcript TP` conforme RM-6, retornando envelope estruturado.
2. Implementar `burnless epoch journal-append --host HOST --host-session-id SID --root ROOT --transcript TP` (`--chat-id` permanece alias Claude legacy). Ele extrai, calcula `exchange_id` determinístico, deduplica e grava JSON imutável sob lock curto por sessão. Preferir UUIDs dos records user/assistant; fallback = hash de transcript path + offsets/identidade dos records, nunca hash apenas do conteúdo (duas trocas textualmente iguais continuam distintas).
3. O lock cobre somente alocação de `seq`, append e commit de checkpoint; nunca fica preso durante chamada LLM. Preferir lock Python testável (`fcntl` em Unix com abstração/fallback), não depender do binário `flock` no macOS.
4. Alterar o Stop hook para ser síncrono no wiring. Ele deve completar `journal-append` antes de retornar e só então disparar `epoch compact-pending` totalmente detached (`</dev/null`, stdout/stderr em log limitado). Remover o duplo async atual.

Envelope mínimo:

```json
{
  "schema": 1,
  "seq": 42,
  "exchange_id": "sha256:...",
  "host": "claude|codex",
  "host_session_id": "old-sid-or-thread-id",
  "process_instance_id": "pilot-or-hook-lineage-id",
  "captured_at": "ISO-8601",
  "user_text": "texto original",
  "assistant_text": "texto final original",
  "files": ["src/x.py"],
  "transcript_path": "/abs/path"
}
```

### RM-4B — Consolidador serial e checkpoint atômico

1. `compact-pending` obtém uma lease de compactador por chat para evitar duas chamadas LLM simultâneas. A lease tem heartbeat/TTL baseado no timeout configurado, mas serve somente para coordenação; nunca prova consolidação e nunca bloqueia o resume.
2. Sob o lock curto de dados, lê checkpoint e journal, determina o lote contíguo `seq > applied_through`, copia os dados necessários e libera o lock durante o LLM.
3. Depois do LLM, readquire o lock e revalida geração/watermark. Se outro compactador avançou, recalcula ou descarta o resultado stale; nunca sobrescreve checkpoint mais novo.
4. Output vazio/inválido mantém checkpoint anterior e deixa journal pendente.
5. Output válido gera um único `checkpoint.json` atômico com `applied_through=max(seq do lote)`. Só depois atualiza mirrors `living.md`, `state.json`, seed e owner cache.
6. O journal não é apagado no commit. Retenção/GC só remove entradas aplicadas antigas depois de pelo menos um checkpoint posterior válido.

### RM-4C — Handoff explícito no `/clear`

1. No adapter Claude, adicionar `burnless_epoch_end.sh` e instalá-lo como `SessionEnd` matcher `clear`.
2. O hook recebe SID/transcript antigos, chama `journal-append` idempotentemente e grava um handoff atômico `{host, old_sid, process_instance_id, root, journal_head, created_at, claimed_by:null}`.
3. `SessionStart(source=clear)` reivindica o handoff fresco e não reclamado do mesmo projeto sob lock, grava `claimed_by=new_sid` e restaura especificamente o `old_sid`. Não usar apenas "diretório mais recente por mtime" para `/clear`.
4. Excluir subagentes/sidechains. Para duas janelas simultâneas, usar um `process_instance_id` estável quando disponível; fallback deve ser claim atômico por projeto + TTL curto e precisa de teste de dois handoffs concorrentes.
5. `init`, `doctor`, `unwire` e testes de wiring precisam conhecer o novo hook. Versões do Claude Code sem `SessionEnd(clear)` mantêm fallback compatível, explicitamente reportado pelo doctor como menor garantia de lineage.
6. A API interna recebe `host`, `host_session_id` e `process_instance_id`; scripts Claude apenas normalizam o payload. O adapter Codex/PTY do P2 usa a mesma API sem simular `SessionEnd` inexistente.

### RM-4D — Restore `checkpoint + delta`, sem espera

1. `epoch resume --source clear` lê o checkpoint canônico do SID entregue pelo handoff.
2. Se `journal_head == applied_through`, injeta somente `living_md`.
3. Se `journal_head > applied_through`, injeta `living_md` mais `## Trocas ainda não consolidadas`, contendo as trocas originais pendentes em ordem.
4. O payload inclui metadados compactos: `checkpoint_generation`, `applied_through`, `journal_head`, `pending_count`, `old_sid` e `new_sid`.
5. Não espere 3s, não rode LLM e não use "lock fresco" no caminho de SessionStart.

### RM-4E — Budget, segurança de parsing e não-recursão

1. Budget separado para delta, default ~2000 tokens/configurável. Preserve integralmente a última pergunta e resposta quando couber.
2. Se exceder, injete começo+fim determinísticos e uma referência local recuperável para o JSON completo; nunca apague o original local.
3. Delimite o delta como conversa histórica, não como novas instruções do sistema.
4. O extractor ignora blocos `[BURNLESS RESTORE]`, `## Trocas ainda não consolidadas`, seed e mensagens internas, impedindo recaptura recursiva.
5. Journal e handoff permanecem locais, com permissões restritas, retenção configurável e sem fallback cloud implícito, preservando privacy-by-architecture.

### RM-4F — Observabilidade

Registrar em `owner_loop.jsonl`/event ledger: `journal_appended`, `compaction_started`, `checkpoint_committed`, `compaction_failed`, `handoff_written`, `handoff_claimed`, `restore_served`, sempre com IDs/watermarks, duração e erro sanitizado. `burnless session` deve mostrar `checkpoint gen`, `applied/head`, `pending` e último erro.

**Verify obrigatório (fault injection):**

1. Rewriter dorme 120s; `/clear` imediato restaura checkpoint + última troca em <1s.
2. Crash antes do rewriter, durante o rewriter, depois do mirror `living.md` e antes/depois do checkpoint: nenhuma troca some.
3. Dois Stops concorrentes: ambos recebem `seq` distinto e entram uma vez no checkpoint.
4. Stop + SessionEnd repetem a mesma troca: dedupe por `exchange_id`, sem seq duplicado.
5. Rewriter vazio/timeout: `applied_through` não avança e delta é servido.
6. Checkpoint já cobre a última troca: restore não repete o original.
7. Duas janelas e dois `/clear`: handoffs não se cruzam.
8. Delta acima do budget: payload truncado deterministicamente e JSON original recuperável.
9. O mesmo teste de journal/checkpoint roda com envelopes normalizados `host=claude` e `host=codex`; nenhum path `~/.claude` ou `~/.codex` aparece no core.
10. Golden scenarios de 20+ turnos/3 rollovers verificam `must_remember` (objetivo, decisões vigentes, arquivos, última validação, próximo passo) e `must_forget` (decisões explicitamente substituídas, threads encerradas, placeholders). A última troca pendente precisa permanecer literal.
11. Bench local registra p50/p95 de `journal-append` e `resume`; nenhum LLM pode aparecer no caminho de restore. Budget inicial de referência: append p95 <=100ms e restore p95 <=250ms em disco local, reportado mas não usado como assert rígido entre máquinas.

## RM-5 · IMPORTANTE — Owner-loop: cache nunca pode acertar após /clear

**Evidência:** `refine-owner` computa fingerprint sobre predecessores **excluindo** o chat corrente S (`epochs.py:688`); após /clear, `carry_forward` computa **incluindo** S (`epochs.py:451-472`). Se S produziu doc não-vazio ⇒ fingerprints divergem ⇒ stale ⇒ floor. Se S ficou vazio ⇒ refine também falhou ⇒ sem cache. Telemetria: 100% `"served":"floor","cache_hit":false` em `owner_loop.jsonl`. Bônus: `refine_seed` chamado com `exchange=""` (`cli.py:1353`).

**Fix:** computar fingerprint de escrita e leitura sobre o MESMO conjunto (incluir o chat corrente no refine — no próximo start ele SERÁ predecessor); ou validar por `(mtime máximo, contagem)` em vez de hash exato. Passar a última troca real como `exchange`.

**Verify:** teste: capture ⇒ refine-owner ⇒ simular novo SID ⇒ `carry_forward_chain` retorna `cache_hit: true` no `owner_loop.jsonl`.

## RM-6 · IMPORTANTE — Extração do transcript: par invertido, poluído e cego a tool_use

**Evidência:** `burnless_epoch_stop.sh:13-42` — o loop sobrescreve `u` e `a` independentemente: fica o último texto user e o último texto assistant, que podem não ser um par (ex.: `<task-notification>` chega como user DEPOIS da resposta ⇒ gravado invertido — comprovado em `epochs/dcf60b96.../ring/001.md`). Perde tool_use/tool_result inteiros (paths editados, comandos, diffs — onde mora o fio operacional) e não filtra `isSidechain`.

**Fix:** reescrever a extração (mover de python-inline no .sh para `burnless epoch extract-exchange --transcript TP`, testável):
1. Último PAR ordenado: última msg assistant com texto + última user ANTERIOR a ela.
2. Filtrar `isSidechain`, `<task-notification>`, `<command-name>`.
3. Anexar linha determinística com `file_path`s dos tool_use do turno (zero-LLM).

**Verify:** teste com transcript sintético contendo task-notification pós-resposta + tool_use ⇒ par correto + linha `files: ...`.

## RM-7 · IMPORTANTE — Consolidação V1: trigger `==10` desarma para sempre; slots `len+1` sobrescrevem

**Evidência:** `epochs.py:65` (`== 10` exato — uma falha do summarizer e o nível passa a 11 ⇒ nunca mais consolida); `epochs.py:49-53,85-89` (slot = `len(files)+1`, não `max+1` ⇒ `{001,003}` ⇒ próximo = `003` ⇒ overwrite silencioso; compare `push_ring` que faz `max+1` correto em `epochs_v2.py:486-488`); `epochs.py:54,90` `write_text` não-atômico; consolidações concorrentes ⇒ `FileNotFoundError` no `rename` com estado meio-consolidado.

**Fix:** `>= 10` no trigger; `max+1` nos slots; escrita tmp+`os.replace`; o lock do RM-4 cobre a concorrência.

**Verify:** `python -m pytest tests/test_epochs.py -q` + testes novos dos 3 casos.

## RM-8 · IMPORTANTE — Marker fantasma `epochs.on`

**Evidência:** o real é opt-out `.burnless/epochs.off` (`epochs.py:231-256`; stop hook linha 12). Mas `docs/COMMANDS.md:67` afirma que `epoch on` cria `.burnless/epochs.on` (falso), `cli.py:1452` e `:1515` leem `epochs.on` que nenhum código cria (⇒ `session`/`explain` reportam `mode: default` com rolling ativa), `scripts/instruction_surface_check.py:46` exige a menção.

**Fix:** unificar em `epochs.off`: `cli.py:1452/1515` → `epochs.is_enabled(...)`; corrigir `COMMANDS.md:67` e o surface check; remover `epochs.on` órfão do repo.

**Verify:** `grep -rn 'epochs\.on' src/ docs/ scripts/ | grep -v test` ⇒ vazio; `burnless session` mostra `rolling` com o engine ativo.

## RM-9 · IMPORTANTE — `/burnless on` não sobrevive ao /clear

**Evidência:** `burnless_mode_hook.sh:48` — `mode_file = session-{sid}.mode`; /clear muda SID ⇒ novo turno lê `off` default (`:53-54`). O usuário liga o modo, dá /clear, e o policy some junto com a memória. Órfãos `session-*.mode` acumulam.

**Fix:** fallback para `~/.burnless/state/last-<project-slug>.mode` (por projeto, com TTL ~24h) quando o arquivo da sessão não existe; escrever ambos no set. GC dos órfãos >7d no mesmo hook.

**Verify:** teste shell: set mode com SID-A ⇒ chamada com SID-B mesmo cwd ⇒ mode `on` mantido.

## RM-10 · IMPORTANTE — `restart_rollover.sh` promove capsule que ninguém escreve; consume-once TOCTOU

**Evidência:** único consumidor de `session-<SID>.rollover.md` é `restart_rollover.sh:51`; grep em `src/` + `templates/` = **zero writers** (writer removido em 2026-06-13, ver `_design/rolling_memory_pure_chat_2026_06_13.md:136`; `tests/test_claude_rollover_hook.py:49` afirma a não-existência). O script sempre respawna SEM seed. E o consume-once de `burnless_session_seed.sh:95-99` deleta o pointer no primeiro SessionStart que casar escopo — subagente/segunda janela pode consumir o seed da sessão errada.

**Fix:** repontar a promoção para `.burnless/epochs/_rolling/seed.md` → `pending_seed.md` (com marker de target); consumo condicionado a `source == "startup"` (ler do stdin JSON). Este item é PRÉ-REQUISITO do modo pty (P2/PTY-0).

**Verify:** `BURNLESS_ROLLOVER_DRYRUN=1 ./restart_rollover.sh` com `_rolling/seed.md` populado ⇒ `pending_seed.md` criado com o conteúdo + marker.

## RM-11 · IMPORTANTE — SessionStart injeta em TODO source; timeout 10s pode zerar a injeção

**Evidência:** `wire_settings_hook` adiciona SessionStart **sem matcher** (`init_claude_code.py:166,170`) ⇒ dispara em startup/resume/clear/compact; `burnless_epoch_session.sh` não lê `source` ⇒ em resume injeta ~8KB de chain num contexto que já a contém. Timeout 10 do hook × `epoch.resume_recon: semantic` (`_semantic_recon` → rewriter llamacpp 120s, `epochs.py:402-422`) ⇒ hook morre ⇒ injeção NENHUMA.

**Fix:** ler `source` do stdin e injetar só em `startup|clear` (em `compact`, versão mínima ou nada); recon semântico NUNCA no caminho do hook — pré-computar no Stop (refine-owner já é o lugar).

**Verify:** teste shell com stdin `{"source":"resume",...}` ⇒ saída vazia; `{"source":"clear",...}` ⇒ chain.

## RM-12 · MENOR — `/usr/bin/jq` hardcoded, PATH frágil, erros descartados

**Evidência:** `burnless_epoch_stop.sh:6-8`, `burnless_epoch_session.sh:3-4` (`/usr/bin/jq` não existe em Linux comum/macOS<15 ⇒ no-op mudo); fallback `$HOME/.local/bin/burnless` (pipx/brew em outro prefixo ⇒ no-op); tudo `2>/dev/null`.

**Fix:** `command -v jq` com fallback python3; log de última falha em `~/.burnless/state/hook_errors.log` (truncado a ~50KB) em vez de descartar stderr; `doctor` reporta esse log.

## RM-13 · MENOR — Workspace `$HOME/antigravity` hardcoded nos templates

**Evidência:** stop:10, session:9. **Fix:** `BURNLESS_WORKSPACE` env ou `~/.burnless/config.yaml` global; default atual como fallback.

## RM-14 · MENOR — Migrar `ring/` para compatibilidade; journal vira a rede de segurança canônica

**Evidência:** `push_ring` grava as 10 últimas trocas cruas (`epochs_v2.py:482-495`), mas somente depois do rewriter no caminho relevante. Nenhum código lê `ring/`; por isso ele não pode ser a fonte de verdade da race do RM-4.

**Fix:** readers novos usam `checkpoint.json + journal`. Para sessões antigas sem journal/checkpoint, importar `ring/` em memória como fallback de compatibilidade, deduplicado por hash e rotulado como provenance legacy. Não escrever novas decisões de commit somente no ring; mantê-lo temporariamente como mirror/retention até uma migração futura.

## RM-15 · MENOR — Mismatch prompt V2×V3, placeholders `<vazio>` no seed

**Evidência:** captura usa prompt de 5 seções (version default 2, `epochs_v2.py:498-512,546-548`) enquanto merge/floor/refine assumem 8 seções V3; docs reais contêm `## Documento completo atualizado` e `<vazio>` literais (`dee58d9d/living.md`, `3afc390b`); filtro só cobre `d\d+: <...` (`epochs.py:519-521`).

**Fix:** `epochs.version: 3` como default (ou no config do projeto); estender filtro (`^<.*>$`, headers fora de SECTIONS); pós-validar saída do rewriter com `parse_living_v3` + rejeição (rejeição cai no fallback do RM-1).

## RM-16 · MENOR — Higiene

- `chat_id` vira componente de path sem sanitização (`epochs.py:16-17`) — sanitizar `[A-Za-z0-9_-]`.
- `owner_cache.py:60`, `retrieve.py:108,201` — `open()` sem `encoding='utf-8'`.
- `echo "$stdin_data"` → `printf '%s'` (stop:6-8).
- Dirs de teste manuais (`AUDITSID`, `FIX2_*`) dentro de `.burnless/epochs/` poluem o ranking por mtime — remover e adicionar ao protocolo de teste usar tmpdir.

---

## Aceitação da fase (teste manual fim-a-fim)

1. `burnless init --claude-code --force`; abrir claude no projeto; trabalhar 5 turnos reais (com edições de arquivo).
2. `/clear` imediatamente após a última resposta.
3. Na sessão nova, o restore contém objetivo corrente, últimos arquivos tocados e próximo passo, incluindo a última troca via `checkpoint + journal delta`, sem esperar consolidação.
4. Com rewriter artificialmente lento por 120s, SessionStart conclui em <1s e a última troca aparece exatamente uma vez.
5. `checkpoint.json.applied_through <= journal_head` sempre; falha/timeout não avança o watermark.
6. `living.md` nunca é zerado; mirrors divergentes não afetam o reader canônico.
7. `/burnless on` antes do `/clear` continua `on` depois.
8. Rodar antes de publicar/commitar: `scripts/public_git_check.sh` e `.venv/bin/python -m pytest`.
9. Rodar o harness de qualidade da memória: nenhuma chave `must_remember` ausente, nenhuma `must_forget` ressuscitada e payload dentro do budget configurado.
