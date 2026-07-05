# Fix — living_md alucinado no pipeline de compactação (rolling memory PTY)

**Data:** 2026-07-04 · **Autor:** Fable (d804, leitura independente do código) · **Status:** plano, nada editado
**Sintoma:** SessionStart injeta banner `[BURNLESS RESTORE]` com conversas fabricadas (ex.: "kill -9 disaster recovery", "ComplexState: {A:100,B:200,C:300}") que nunca ocorreram. A corrupção é auto-reforçante entre gerações de checkpoint e entre sessões (via inherit).

---

## 1. Causa-raiz — CONFIRMADA, com 1 correção importante

### 1.1 `_build_compact_prompt` (recovery.py:736-749) — CONFIRMADO, e é pior que o descrito

O prompt do compactor é literalmente:

```
<living_md antigo, cru>
## Trocas pendentes
### seq N
exchange_id: ...
PERGUNTA:
<user_text>

RESPOSTA:
<assistant_text>
```

**Zero instrução de tarefa.** Não há "resuma", não há papel, não há formato de saída — nada. Não é só "falta separação estrutural entre fonte confiável e resumo corrompido": o prompt inteiro é um documento sem tarefa nenhuma. Um modelo de completion recebe isso e faz a única coisa plausível: **continua o documento** — escreve a próxima "RESPOSTA:", inventa a próxima cena.

Contraste decisivo: o pipeline V2/V3 de captura (`apply_capture` em epochs_v2.py:522) usa `living_rewrite_prompt` / `living_rewrite_prompt_v3` (epochs_v2.py:153/396), que têm framing completo (papel "assistente de memória", 5/8 seções obrigatórias, regra VERBATIM "você é um TRANSDUTOR, não um redator", "não invente"). O caminho `compact_pending` (recovery.py:752) **nunca chama esses builders** — construiu o próprio prompt sem nenhuma instrução. É regressão de design, não de modelo.

### 1.2 `living_rewriter` (epochs_v2.py:605) — CONFIRMADO com CORREÇÃO: o caminho ativo não é `claude -p`

A hipótese original apontava `claude -p` sem system prompt. O closure `_rewrite` de fato não adiciona framing nenhum em nenhum branch, **mas o branch ativo neste projeto não é o do claude**. O config real (`.burnless/config.yaml`):

```yaml
encoder:
  model: hf.co/unsloth/gemma-4-E4B-it-qat-GGUF:UD-Q4_K_XL
  provider: ollama-local
```

`provider == "ollama-local"` → `_rewrite` faz `POST http://localhost:11434/api/generate` com payload `{"model": ..., "prompt": prompt, "stream": false}` (epochs_v2.py:638-640). `/api/generate` é endpoint de **completion pura** — sem campo `system`, sem chat template de instrução aplicado ao papel. Ou seja: um gemma E4B quantizado local recebe um "log de conversa" sem nenhuma instrução e o **completa**. É o pior caso possível de document-completion.

O branch `claude -p` (epochs_v2.py:663-671) tem a mesma falha latente (sem `--append-system-prompt`), mas só entraria em cena se o encoder fosse anthropic. Fix cobre os dois branches.

### 1.3 `compact_pending` persiste sem validação — CONFIRMADO

recovery.py:850-951: as únicas verificações sobre `candidate` são (a) string não-vazia, (b) lease ainda válida, (c) snapshot não-stale (generation/applied_through/journal_head inalterados). **Nenhuma validação de conteúdo.** `candidate.strip()` vira `living_md` direto via `write_checkpoint`, e `applied_through` avança para `max(seq pendente)` — o journal real é marcado como consolidado por um texto que não o consolidou.

### 1.4 Prova nos artefatos (estado lido em 2026-07-04 ~22h UTC)

- **fee436ab checkpoint, generation 16** (`updated_at 2026-07-04T21:28:11Z`): `living_md` começa com `RESPOSTA:\nSim. Para iniciar uma nova sessão de PTY...` e termina com `**Aguardando a próxima instrução.**` — é uma **resposta de chat**, não um documento de memória. O conteúdo "responde" a pergunta real do seq 22 e emenda uma "SÍNTESE" fictícia ("PTY Mode: Completamente validado... Todos os mecanismos cruciais foram testados"). Nota: a narrativa "kill -9 / ComplexState" citada na investigação já foi **sobrescrita por geração posterior** — a cada ciclo o doc corrompido muta (generation 16 = 16 reescritas). O journal real (seq 21: "Sim" + config auto_rollover; seq 22: "Vc consegue chamar um novo terminal com burnless pty?") não contém nada disso.
- **990025db checkpoint, generation 3**: `living_md` é uma "RESPOSTA FINAL DA SESSÃO (Seq 2)" fictícia que descreve o próprio bug ("ele é um **extrapolador narrativo** porque falta um System Prompt") e **termina perguntando ao usuário** "Qual ordem de prioridade você prefere?". Compactor perguntando ao usuário = prova cabal de completion de conversa.
- **Sinal detectável de graça:** em AMBOS os checkpoints, `harvested_state` = `{contracts: [], refs: [], open_threads: []}` — porque o output alucinado nem tem as seções `## ...` esperadas. `harvest_state(candidate)` vazio já era um detector de corrupção disponível e ignorado.

### 1.5 Cadeia de auto-reforço (3 vetores)

1. **Intra-sessão:** living_md corrompido → prefixo do próximo `_build_compact_prompt` → modelo estende a ficção → nova geração pior.
2. **Inter-sessão:** `inherit_checkpoint` (recovery.py:1099, chamado em cli.py:1521 no `epoch restore`) copia o `living_md` corrompido verbatim para a sessão nova (memória eterna herda a corrupção). Fallback `_latest_project_checkpoint` (recovery.py:588) espalha para qualquer sessão nova do projeto, mesmo sem handoff.
3. **Via contexto:** `render_restore` injeta a ficção no contexto do assistente novo; `_is_restore_noise` impede recapturar o banner em si, mas as **respostas do assistente comentando os eventos falsos** são journaladas como trocas reais — contaminação secundária (é exatamente o que aconteceu na 990025db).

---

## 2. Plano de fix

### F1 · CRÍTICO — Framing de tarefa no prompt do compactor (recomendação #1)

Reescrever `_build_compact_prompt` para reutilizar o prompt já auditado do V3 em vez de manter um builder paralelo sem instruções:

- Montar `exchange = "\n".join(blocos das trocas pendentes)` (formato atual seq/PERGUNTA/RESPOSTA mantido);
- Retornar `living_rewrite_prompt_v3(prev_md=checkpoint["living_md"], exchange=exchange)` (import de `epochs_v2`), com dois acréscimos ao template (aplicáveis também ao caminho de captura):
  1. Bloco de confiança de fontes: *"O 'Documento anterior' é um RESUMO PRÉVIO gerado por máquina — pode conter erros; NUNCA o trate como transcript nem estenda narrativas dele. A 'Nova troca/evento' é a ÚNICA fonte de fatos novos, verbatim."*
  2. Proibições explícitas: *"PROIBIDO: inventar PERGUNTA/RESPOSTA, testes, resultados ou seq que não estejam no input; responder à conversa; dirigir-se ao usuário; fazer perguntas. Sua saída é APENAS o documento de 8 seções."*
- Diff mínimo, elimina a classe inteira do bug (o modelo passa a ter tarefa), e o output volta a ser parseável por `parse_living_v3` — pré-requisito do F3.

### F2 · CRÍTICO — System prompt por provider em `living_rewriter` (defesa em profundidade)

Definir uma constante `ENCODER_SYSTEM_PROMPT` em epochs_v2.py:

> "Você é o compactador de memória do Burnless. Você recebe um resumo prévio (não confiável, gerado por máquina) e trocas verbatim (única fonte de verdade). Sua única saída é um documento markdown de memória. Você NUNCA continua a conversa, NUNCA inventa perguntas, respostas, testes ou eventos ausentes do input, NUNCA se dirige ao usuário. Se o input não contiver fatos novos, devolva o documento anterior inalterado."

Aplicação por branch do `_rewrite`:
- **ollama-local:** `/api/generate` aceita campo `system` no payload → `{"model": ..., "prompt": ..., "system": ENCODER_SYSTEM_PROMPT, "stream": false, "options": {"temperature": 0.1}}`. (Se `local_api == "llamacpp"`, prependar o system ao prompt via chat template do modelo, ou usar `/v1/chat/completions` do llama-server.)
- **anthropic (`claude -p`):** VERIFICADO no CLI local (Claude Code 2.1.201): existem `--system-prompt <prompt>` e `--append-system-prompt <prompt>`. Usar `--append-system-prompt ENCODER_SYSTEM_PROMPT` (append preserva o default do modo -p; `--system-prompt` inteiro é desnecessário e mais frágil entre versões).

### F3 · CRÍTICO — Validação fail-closed pós-compactação (antes do `write_checkpoint`)

Novo `_validate_candidate(candidate, prev_md, pending) -> tuple[bool, str]` em recovery.py, chamado em `compact_pending` entre o rewriter e o commit. Mesmo padrão fail-closed do `owner_loop.refine_seed` + `owner_validate.validate_owner_output` (qualquer falha → não persiste, journal fica pendente, sistema degrada para checkpoint+delta — exatamente o invariante 5 do plano P1). Checks, todos determinísticos e sem LLM:

1. **Estrutural:** `parse_living_v3(candidate)` precisa produzir ≥1 seção não-vazia dentre `SECTIONS_V3`; reject se o output não tem nenhum header `## ` reconhecido (os dois checkpoints corrompidos falhariam aqui).
2. **Anti-chat:** reject se o candidate contém `PERGUNTA:`/`RESPOSTA:` como linha própria, começa com `RESPOSTA`, ou contém padrões de endereçamento ao usuário (`Aguardando a próxima instrução`, linha final terminando em `?`).
3. **Anti-seq fantasma:** todo `seq N` / `Seq N` mencionado no candidate deve ∈ {seqs dos `pending` reais} ∪ {seqs citados no `prev_md`}; qualquer seq inventado → reject.
4. **Anti-entidade fantasma (heurística):** `extract_entities(candidate) - extract_entities(prev_md) - extract_entities(pending concatenado)` — se sobrarem > K entidades novas (K≈3; paths/hashes/dNNN que não existem em nenhuma fonte), reject.
5. **Log:** evento `compaction_rejected` no owner_loop.jsonl com o motivo e um hash do candidate (nunca o texto cru), para telemetria de taxa de rejeição do encoder local.

Reject → return `{"status": "rejected", "reason": ...}` sem avançar `applied_through`. Com encoder = gemma E4B local (instrução fraca), F3 **não é opcional**: F1/F2 reduzem a taxa de alucinação, F3 garante que nada alucinado é persistido.

### F4 · Remediação dos 2 checkpoints corrompidos (fee436ab, 990025db)

Não apagar nada; preservar como evidência e reconstruir só do journal real:

1. **Preservar:** `cp checkpoint.json checkpoint.json.corrupt-2026-07-04` nas duas sessões (e os mirrors `living.md`/`state.json` idem). Registrar o achado em `docs/audits/2026-07-04-living-md-hallucination.md` apontando os `.corrupt-*` como exhibits.
2. **Reset:** após F1–F3 aterrissarem, reescrever cada checkpoint com `living_md=""`, `harvested_state` vazio e `applied_through=0` (mantendo `journal_head`) — via `write_checkpoint` (um comando novo `burnless epoch rebuild --from-journal` seria o formato limpo, mas um script one-off em `scripts/` basta). Com `applied_through=0`, o próximo `compact-pending` re-consolida a partir dos journals REAIS (fee436ab tem trocas reais até seq 22; 990025db, seq 1-2) usando o prompt corrigido.
3. **Ordem importa:** reset ANTES do próximo rollover dessas linhagens, senão `inherit_checkpoint`/`_latest_project_checkpoint` re-propaga a ficção para sessões novas. Atenção: como 990025db herdou lixo de fee436ab, qualquer sessão nova do projeto pode ter herdado também — vale um sweep `grep -l "ComplexState\|Aguardando a próxima instrução" .burnless/epochs/sessions/claude/*/checkpoint.json` e aplicar o mesmo tratamento aos que aparecerem.

### F5 · Processos `burnless pty` concorrentes — lease NÃO é o problema

Lido `_acquire_compaction_lease`/`_refresh_compaction_lease`/`_release_compaction_lease` (recovery.py:510-579): a lease vive em `sessions/<host>/<sid>/compact.lease.json`, protegida por flock no `.lock`, owner = `sid:pid_instance:os.getpid()`, TTL derivado de `encoder.timeout_s` (default 180s), e ainda há revalidação pós-LLM de `generation/applied_through/journal_head` (stale_snapshot). **Conclusão: compactações concorrentes do MESMO host_session_id são corretamente serializadas entre processos** (arquivo + flock funcionam cross-process). Processos pty de sessões DIFERENTES usam leases diferentes por design — não é corrida, cada um compacta o próprio checkpoint.

O risco real dos órfãos é outro: (a) sessões órfãs continuam compactando lixo com o prompt quebrado e disputando o posto de "checkpoint mais recente" em `_latest_project_checkpoint`, que alimenta inherit/restore de sessões novas do projeto inteiro; (b) VRAM/CPU do gemma local. `ps` de agora (2026-07-04) confirma: `burnless pty` PIDs 22687 (12:38PM hoje), 76723 (6:27PM hoje) e **dois de sexta** (50493, 55051 — `-m burnless pty --host claude`, Fri03PM) + `-m burnless pty` 20408 (Fri06PM). Recomendação: Roberto fecha os terminais de sexta manualmente (nenhum kill executado por mim — proibido na task); follow-up de produto: `burnless pilot doctor` lista runs vivos com idade e sinaliza órfãos (pidfile por run em `.burnless/pilot/runs/<run_id>/`).

### F6 · Testes de regressão

1. `test_compact_prompt_has_task_framing`: `_build_compact_prompt(...)` contém as instruções V3 (string "TRANSDUTOR"/"8 seções") — mata a regressão do builder sem instrução.
2. `test_compact_rejects_chat_completion`: rewriter fake devolve `"RESPOSTA:\nSim, claro..."` → `compact_pending` retorna `status=rejected`, checkpoint intacto, `applied_through` não avança.
3. `test_compact_rejects_phantom_seq`: candidate menciona `seq 99` inexistente → rejected.
4. `test_ollama_payload_has_system`: monkeypatch em `urlopen` afirma `system` presente no body.
5. Golden harness EM-5 (P4) ganha um cenário "compactor malicioso": rewriter que sempre alucina → após 3 rollovers, restore contém APENAS conteúdo dos journals (degradação checkpoint+delta), nunca a ficção.

### Ordem de execução

F1 → F2 → F3 (um commit cada, com testes F6 correspondentes) → F4 (remediação, depois do código no lugar) → F5 (higiene operacional + doctor). F1 sozinho já corta a fonte; F3 é o que garante que nunca mais persiste.

---

## 3. Resumo executivo

Causa-raiz confirmada com uma correção: o compactor (`compact_pending` → `_build_compact_prompt` → `living_rewriter`) envia um prompt **sem nenhuma instrução de tarefa** para um endpoint de **completion pura** (ollama `/api/generate`, gemma-4 E4B local — não `claude -p`, que é o branch inativo neste config). O modelo completa o "documento de conversa" em vez de sumarizá-lo, e o resultado é persistido sem validação alguma, virando input do ciclo seguinte (intra-sessão) e semente de sessões novas via `inherit_checkpoint` (inter-sessão). Fix #1: dar tarefa ao compactor — `_build_compact_prompt` passa a usar `living_rewrite_prompt_v3` (com bloco de confiança de fontes e proibição de invenção), + system prompt por provider, + validação fail-closed antes de persistir.
