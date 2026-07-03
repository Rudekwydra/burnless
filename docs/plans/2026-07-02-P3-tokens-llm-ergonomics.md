# P3 — Tokens auditáveis + ergonomia LLM (MCP/CLI)

**Parte A (TK-\*):** hoje existem 5 acumuladores independentes que não batem entre si: `burnless_tokens`+`by_source` (metrics.json/audit.jsonl), `estimated_cost_avoided_usd`, savings por run (capsule/turns.jsonl), usage REAL do worker (parseado e descartado), e quota de assinatura (nunca cruzada). `metrics`, `economy`, `status` e `session` dão 4 respostas diferentes para "quanto economizei". O usage real que o sistema JÁ captura morre sem persistência.

**Parte B (MCP-\*):** a superfície MCP é uma reimplementação paralela do CLI que diverge onde os docs prometem equivalência; o fluxo canônico dos docs (`do`, epoch) nem existe no MCP.

---

# Parte A — Token accounting

## TK-1 · CRÍTICO — Double counting do mesmo log em toda delegação

**Evidência:** `exec/runner.py:783-791` credita `raw_logs_isolated = estimate_tokens(stdout+stderr)` (log inteiro, TODA run, inclusive ERR/PART); `exec/runner.py:1146-1159` credita `capsule_compression = raw_tokens − capsule_tokens` **do mesmo log**. Log de 100k ⇒ ~200k "burnless tokens". Os dois contrafactuais são o MESMO token evitado.

**Fix:** manter `capsule_compression` como o único crédito em `burnless_tokens`; rebaixar `raw_logs_isolated` para contador informativo fora do headline (ou creditá-lo como `raw − capsule` e matar o outro). Migração: script one-shot que anota `metrics.json` com `schema: 2` sem tentar corrigir histórico (rotular como pré-fix).

**Verify:** teste: run com log X e capsule Y ⇒ `burnless_tokens` cresce exatamente `estimate(X)−estimate(Y)`.

## TK-2 · CRÍTICO — Estimativa e real misturados sem rótulo

**Evidência:** no mesmo `burnless_tokens`: chars/4 (`estimator.py:4`, `runner.py:783`), chars/3.5 (`metrics.py:60`; `codec/encoder.py:273-279`; `codec/decoder.py:220-227`), e cache_read REAL (`metrics.py:315`, via `cached_worker.py:474`). Linhas do audit.jsonl não carregam método.

**Fix:** campo `basis: "chars4"|"chars3.5"|"api_usage"` em cada entry (`metrics.record()`, `metrics.py:149-157`); dashboards mostram `measured` vs `estimated` separados.

## TK-3 · CRÍTICO — `estimated_cost_avoided_usd` reprecifica o histórico à taxa do último writer

**Evidência:** `metrics.py:143-145` recomputa `burnless_tokens/1M × usd_per_million` do call corrente; `record_encoder_call`/`record_decoder_call`/`record_brain_call` hardcodam `15.0` (`metrics.py:206-208,263-265,324-326`); `cli.py:514` hardcoda 15 de novo. O total em $ oscila retroativamente e precifica a $15/MTok tokens evitados em bronze/ollama (~$0 real).

**Fix:** acumular `usd += amount × taxa_do_evento` por evento; nunca recomputar o total; taxa do evento derivada do worker real via `pricing.rate()` (com TK-10).

## TK-4 · IMPORTANTE — Baseline `expensive_model_avoided` inflado por construção

**Evidência:** `cli.py:257-271`: TODA delegação bronze/silver (bronze é default, `routing.py:26-34`) credita `estimate_tokens(body)` como "evitou opus" — contrafactual falso (o default nunca foi opus) e o corpo foi consumido de verdade pelo worker barato. `economy.py:64-66` já faz o delta certo (opus−haiku); o headline soma o valor cheio.

**Fix:** creditar só em de-escalação real (tier natural > efetivo); valor = delta de preço, não valor cheio; `basis: estimated`.

## TK-5 · IMPORTANTE — `metrics` × `economy` × `status` × `session` divergem

**Evidência:** `economy.py:54-96` ignora `raw_logs_isolated` (o maior bucket) e precifica tudo a opus; `metrics.py:15` + `dashboard.py:16`: `token_burn_avoided_percent` que nenhum código escreve (sempre 0%); `cli.py:1464-1488` + `session_hud.py:29-36`: HUD lê `state["savings"]`/`state["turns"]` que nenhum código escreve (runner grava `turn_counter`, `runner.py:1171`).

**Fix:** um módulo `ledger.py` como fonte única (lê audit.jsonl + spend.jsonl do TK-7); os 4 comandos viram renderizações dele. Remover o percent morto ou calculá-lo de verdade; HUD passa a ler o ledger.

## TK-6 · IMPORTANTE — Keepalive quebra o invariante e conta ping pago como economia

**Evidência:** `metrics.py:411-433`: `increment_keepalive_ping` soma em `by_source["keepalive_cache_renewed"]` mas NÃO em `burnless_tokens` e NÃO escreve audit.jsonl; `economy.py:69-73` precifica esses tokens a $13.5/MTok como economia sem subtrair o custo do ping.

**Fix:** registrar via `record()` (ganha trilha) e subtrair `keepalive_cost_usd` na economy. Se PTY-7 deletar o keepalive, aposentar os contadores nos dois lugares.

## TK-7 · IMPORTANTE — Usage REAL parseado e jogado fora; leitor real é código morto

**Evidência:** `agents.py:54-77,850` extraem usage do stream-json; `live_runner.py:84-93` só imprime `[usage]` no log; `execute_delegation` nunca persiste `result["usage"]` (zero consumidores em `exec/runner.py`); `usage_meter.py` (leitor dos jsonl reais do Claude Code) tem **zero callers**.

**Fix (núcleo da auditoria de tokens que o dono quer):**
1. `result["usage"]` → `capsule.tokens.actual` + record do audit_graph + append em `.burnless/spend.jsonl` (`{ts, did, tier, provider, model, usage{in,out,cache_read,cache_write}, duration_s, backend, retry}`).
2. Ligar `usage_meter` ao `burnless session`/`status` (gasto real da sessão de chat).

**Verify:** após uma run: `jq . .burnless/spend.jsonl | tail -1` contém usage não-nulo; `burnless read dXXX` mostra `tokens.actual`.

## TK-8 · IMPORTANTE — `metrics.json` read-modify-write sem lock

**Evidência:** `metrics.py:112-173` (load→mutate→save) num pipeline que suporta `burnless do` paralelo (`parallel_jitter`, `alloc_delegation_id` com lock em `state.py:82-89`). Incrementos se perdem silenciosamente.

**Fix:** reusar `_exclusive_lock` do state — ou melhor: tornar `metrics.json` cache reconstruível do audit.jsonl (append-only é à prova de race), `burnless metrics --rebuild`.

## TK-9 · IMPORTANTE — audit_graph não permite reconstruir gasto

**Evidência:** `audit_graph.py:9-50` + `runner.py:509-522`: record tem status/files/hashes/verify mas nada de tier/provider/model/usage/duração; `runs/{did}.plan.json` (`runner.py:630-641`) tem tier/provider mas não usage e não é linkado.

**Fix:** schema_version 2 do record com `tier, provider, model, usage, duration_s, retry_count, backend` (tudo disponível no ponto de emissão, `runner.py:1173`); `burnless audit --session` ganha agregação por tier/provider/dia. Casa com TK-7 (mesma fonte).

## TK-10 · MENOR — pricing congelado, fallback opus, assinatura ignorada

**Evidência:** `pricing.py:1` "Jan-2026" (6 meses atrás); `fable: 10/50` sem caveat; modelo desconhecido → fallback **opus** silencioso (`pricing.py:76`) — infla economia; nada distingue usuário API de assinatura (onde $ marginal ≈ 0).

**Fix:** `PRICES_AS_OF` exibido nos outputs; fallback → taxa 0 + warning; `billing: subscription` no config troca $ por % de quota (dados de `subscription_usage.py`, hoje órfão).

## TK-11 · MENOR — savings_footer engana em 3 pontos

**Evidência:** tiktoken `cl100k_base` (tokenizer OpenAI) para modelos Claude (`savings_footer.py:37-45`); rótulo `Real:` para o contrafactual (`:121`; `economy.py:211` já usa `[solo=estimado]`); `runner.py:1229-1230` mapeia tier→família Claude ignorando o worker real (gold em codex precificado como opus).

**Fix:** `Baseline (est.):` no rótulo; família via `pricing.model_family(worker_model)` real; documentar o tokenizer como aproximação.

## TK-12 · MENOR — JSONLs sem rotação; leitores carregam tudo

**Evidência:** `audit.jsonl`, `events.jsonl`, `audit_graph.jsonl`, `~/.burnless/global_metrics.jsonl`, `~/.burnless/turns.jsonl` sem bound; `read_audit` lê tudo e fatia no fim (`metrics.py:459-474`); `usage_meter.py:149` `break` só sai do arquivo corrente.

**Fix:** rotação por tamanho (~5MB) ou mês; leitura tail-first.

## TK-13 · MENOR (mas o maior retorno/custo) — `metrics --explain` prometido e inexistente

**Evidência:** `metrics.py:5` promete "`burnless metrics --explain` can show line-by-line where the number came from"; a flag não existe (`cli.py:1641-1659`); `dashboard.render_audit` (`dashboard.py:125`) é órfão; audit.jsonl é escrito e nunca lido por comando algum.

**Fix:** `burnless metrics --explain [--limit N]` = `render_audit(read_audit(...))` com coluna `basis` (TK-2). **Fazer primeiro na Fase 2** — é a promessa central de auditabilidade e está 90% pronta.

## TK-14 · MENOR — `metrics --global` subconta; bucket `compact_state` morto

**Evidência:** só `record()` escreve global (`metrics.py:162-171`); encoder/decoder/brain/keepalive não (`:176-345`); `compact_state` sem writer, sempre 0 em metrics/economy.

## TK-15 · MENOR — `savings_formula.compute()` lê campo do lugar errado

**Evidência:** `savings_formula.py:40` lê `delegation_counter` do dict de metrics; o campo vive em state.json (`state.py:12`) ⇒ termos history/quadratic sempre 0. **Fix:** corrigir a fonte ou deletar o legacy exportado.

---

# Parte B — Ergonomia MCP/CLI para LLMs

## MCP-1 · CRÍTICO — `run(background=true)` quebrado desde sempre

**Evidência:** `mcp_server.py:221` chama `resolve_command(burnless_root, "run", id)`; a função (`agents.py:563`) aceita UM argumento ⇒ `TypeError` ⇒ toda chamada retorna `{"error":"worker_failed","hint":"resolve_command() takes 1 positional argument..."}`. Mesmo com arity certa, montaria o comando do worker cru, pulando capsule/audit/metrics.

**Fix:** `subprocess.Popen([sys.executable, "-m", "burnless", "run", id], cwd=project_root, start_new_session=True, stdout=log)`.

**Verify:** teste MCP: `run(id, background=true)` ⇒ `{"status":"running","pid":...}` e capsule aparece ao fim.

## MCP-2 · CRÍTICO — Contrato fantasma `runs/{id}.envelope.json`

**Evidência:** `mcp_server.py:234` devolve `envelope_path` e `:304` (fallback 2 do `read`) lê esse arquivo — **nenhum código o escreve** (grep: só esses 2 pontos). O "3-paths fallback" anunciado nunca acerta o caminho do meio.

**Fix:** runner grava o summary como envelope em `runs/` OU o fallback aponta para `temp/{id}.json` (que existe, `runner.py:1128`). Alinhar a description da tool.

## MCP-3 · IMPORTANTE — `delegate` via MCP é outro produto que via CLI

**Evidência:** `mcp_server.py:104-137`: `goal="Task delegation"` hardcoded (`:125`); pula `spec_validator` (paths absolutos, verify fence — `cli.py:178-200`); pula escalation policy (`cli.py:202-206`); não credita métricas; não seta `last_delegation`; `state_mod.save` sem mutação (`:137`) pode clobber estado concorrente. Mesmo verbo, semântica diferente — a pior pegadinha para um modelo.

**Fix:** extrair o corpo de `cmd_delegate` para função pura compartilhada; recusas de validação viram `{"error":"invalid_spec_paths","hint":"use absolute paths; ex: /abs/..."}` no MCP.

## MCP-4 · IMPORTANTE — Fluxo canônico impossível só com MCP

**Evidência:** o hook manda "ONLY delegate via burnless **do**/delegate" (`docs/USING_BURNLESS_FROM_YOUR_LLM.md:175`) — não há tool `do` no MCP (`mcp_server.py:533-662`); `epoch` (rolling memory) não está exposto; `metrics`/`economy`/`session` também não.

**Fix:** tools novas: `do(text, tier?, timeout?)` → `{id, status, done_report}`; `epoch(action: capture|read|resume, chat_id)`; `metrics()` compacta (headline + by_source + basis split).

## MCP-5 · IMPORTANTE — Vazamento de volume nos outputs MCP

**Evidência:** `status` project-wide devolve o **config inteiro** a cada poll (`mcp_server.py:434`); `pending_delegations` sem cap (`:396-401`); `audit` devolve todos os records (`:444-445`); `retrieve` sem cap de resultados (`:469-475`).

**Fix:** `status` ⇒ ~10 linhas (counts + last_capsule + tiers resolvidos), `include_config: false` default; `limit` com default baixo em audit/retrieve. Efeito: poll de status cai de ~2-4k tokens para ~200.

## MCP-6 · MENOR — `_run_sync` descarta o done-report de 1 linha

**Evidência:** `mcp_server.py:249-252` captura stdout do runner e joga fora — perdendo o `OK:d123 · files 2 · verify 3/3 · <answer_hint>` (`runner.py:1210`) que o fluxo CLI otimizou para o Maestro responder sem `read`. **Fix:** incluir `done_report` no retorno; envelope opcional.

## MCP-7 · MENOR — Hints de erro crus; `capsule_not_ready` sem próximo passo

**Evidência:** hints com `str(exception)` crua (`mcp_server.py:149,237,272,477`); `capsule_not_ready` não distingue "id não existe" de "ainda rodando" nem aponta `status`/`run` (`:282`). Positivo: padrão `{error, hint}` consistente, sem stack trace vazando. **Fix:** hints sempre com ação ("run `status(id)` to check; if missing, `delegate` first").

## MCP-8 · MENOR — Descriptions/schemas telegráficos

**Evidência:** "Read delegation output (3-paths fallback)" (`:588`) não descreve o retorno; `delegate.text` não avisa paths absolutos / `## Verify` fenced — regras que o CLI rejeita com exit 6. **Fix:** colocar o contrato na description (campos do envelope, regra de paths, `capsule` vs `read`); elimina uma rodada de tentativa-erro por tool.

## MCP-9 · Docs × realidade (sincronizar)

- **C1 (importante):** `USING_BURNLESS_FROM_YOUR_LLM.md:31` — "`burnless run` — runs the most recent queued delegation": falso, `id` é posicional obrigatório (`cli.py:1595-1596`); `burnless read [<id>]` idem (`:1717-1719`). Modelo seguindo o manual recebe erro de argparse.
- **C2 (menor):** docs listam 3 tiers (USING:44-55, `llms.txt:15`); existe **diamond** (`cli.py:1569`, mcp:543). Tabela de keywords não menciona path-hint→silver (`routing.py:39-41`).
- **C3 (menor):** `llms.txt:39-41,85` anuncia L3 cipher + "capsule v2 `burnless:v2:...`" — deprecated/removido do Free (`cli.py:783-802`).
- **C4 (menor):** `docs/mcp.md` lista 6 tools; existem 10; "read (waits for completion)" é falso (`read` não espera).

**Regra permanente:** `docs/COMMANDS.md` já declara "verified against --help wins"; estender o `scripts/instruction_surface_check.py` para validar llms.txt/mcp.md contra a lista real de tools/flags (teste em CI).

---

## Aceitação da fase

1. `burnless metrics --explain` mostra linha a linha com `basis` (estimado vs medido) — e os totais batem com `metrics`.
2. `burnless audit --session` responde "quanto gastei hoje, por tier e provider" com usage REAL.
3. Um LLM só-com-MCP executa: `do` → `status` → `read` → responder do `done_report`, sem shell e sem nenhum erro de contrato.
4. `status` MCP ≤ ~300 tokens por chamada.
