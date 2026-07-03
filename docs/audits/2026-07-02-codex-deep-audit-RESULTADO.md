# Auditoria profunda Burnless — Codex deep audit — 2026-07-02

Escopo: leitura adversarial de `src/burnless/agents.py`, `src/burnless/live_runner.py`, `src/burnless/cli.py`, `src/burnless/config.py`, `src/burnless/exec/runner.py`, `src/burnless/routing.py`, `src/burnless/spec_validator.py`, `src/burnless/warm_session.py`, `src/burnless/warm_session_codex.py`, warm daemon e testes relacionados.

Contexto: os dois bugs já corrigidos em `live_runner.py` (dedup de flags valueless e injeção de flags claude-only em Codex) não são listados como achados novos. Eles aparecem só como evidência do padrão de duplicação.

## Resumo

- Crítico: 3
- Importante: 6
- Menor: 3

## Achados Críticos

### 1. Crítico — `--gold/--silver/... codex:MODEL` não passa o modelo ao Codex

**Evidência**

- `src/burnless/config.py:304-309`: `build_worker_agent("codex", model)` grava `name=model`, mas o `command` é sempre `codex exec --skip-git-repo-check --sandbox danger-full-access`, sem `--model`, `-m` nem campo `model`.
- `src/burnless/agents.py:581-589`: `_extract_model_from_parts()` só detecta `--model` ou `-m` no comando.
- `src/burnless/live_runner.py:329-334`: quando o modelo não é extraído do comando, o warm path cai para `DEFAULT_PROVIDER_MODELS["codex"]` (`gpt-5.2` em `src/burnless/config.py:430-433`).
- `tests/test_worker_override.py:45-50` e `tests/test_worker_override.py:100-111`: testes só validam `provider`, `name` e presença de `codex exec`; não validam que o modelo do spec entra no comando.
- Contraste: `src/burnless/provider_autodetect.py:34-40` já tem `_codex_cmd(..., model=...)` que inclui `-m {model}`, mas `config.build_worker_agent()` reimplementa outro template.

**Impacto**

O usuário pode chamar `burnless do --tier gold --gold codex:gpt-5.5 ...` e acreditar que está usando `gpt-5.5`, mas a invocação gerada não fixa esse modelo. Na prática depende do default externo do Codex CLI; no warm resolver do Burnless, o modelo inferido vira `gpt-5.2`. Isso invalida o contrato do override e distorce métricas/warm pool por modelo.

**Recomendação**

Fazer `build_worker_agent("codex", model)` usar a mesma função/template de `provider_autodetect._codex_cmd()` ou um helper central comum, incluindo `-m {shlex.quote(model)}` e preservando sandbox. Adicionar teste que `build_worker_agent("codex", "gpt-5.5")["command"]` contenha `-m gpt-5.5` ou `--model gpt-5.5`, e teste de wiring que `_extract_model_from_parts(resolve_command(agent)) == "gpt-5.5"`.

### 2. Crítico — Override “for this run only” escreve no `config.yaml` real e pode persistir por crash ou vazar para runs paralelas

**Evidência**

- Help: `src/burnless/cli.py:1840-1845` documenta `--diamond/--gold/--silver/--bronze` como “override the ... worker for this run only”.
- `src/burnless/cli.py:1126-1135`: `cmd_do()` lê o texto original, aplica `apply_worker_overrides()` e chama `config_mod.save(p["config"], _cfg_w)`, ou seja, escreve no `.burnless/config.yaml` real.
- `src/burnless/cli.py:1149-1154`: restauração ocorre só no `finally` do mesmo processo.
- `src/burnless/cli.py:1112-1114`: a delegação é criada antes do patch, então há estado parcial mesmo se o patch/run falha.
- `src/burnless/exec/runner.py:420-424`: `execute_delegation()` relê `config.yaml` durante a janela do patch.
- `src/burnless/cli.py:278-297`: o jitter/lock protege o run por `did`, não o arquivo de config global do projeto; duas invocações `cmd_do` podem observar ou sobrescrever o patch uma da outra.

**Impacto**

O comportamento relatado (“this run only” persistiu no projeto aeomachine) é bug, não design. Mesmo com `finally`, `SIGKILL`, crash do interpretador, queda de energia ou exceção antes de `_orig_config_text` ser restaurado deixa o override em disco. Em paralelo, outra delegação pode carregar o override temporário e rodar com worker errado; a restauração textual também pode apagar mudanças legítimas feitas por outro processo enquanto a run estava ativa.

**Recomendação**

Não patchar arquivo. Passar o cfg efetivo em memória para `execute_delegation()` ou adicionar `RunOpts.worker_overrides` / `RunOpts.config_override` e aplicar antes de selecionar `agent_cfg`. Alternativa menor: criar um arquivo temporário de config por run e apontar o runner para ele, mas ainda evitar tocar `.burnless/config.yaml`. Adicionar teste de `cmd_do` que monkeypatcha `cmd_run` e afirma byte-a-byte que `p["config"]` não muda durante e após o run.

### 3. Crítico — Warm daemon Codex nunca refresca porque `is_alive` e `needs_refresh` são mutuamente exclusivos no limite

**Evidência**

- `src/burnless/warm_session_codex.py:32-33`: `TTL_S = 300` e `HEARTBEAT_INTERVAL_S = 300`.
- `src/burnless/warm_session_codex.py:281-294`: `is_alive()` retorna `age < ttl_s`.
- `src/burnless/warm_session_codex.py:297-310`: `needs_refresh()` retorna `age >= heartbeat_interval_s`.
- `src/burnless/warm_daemon.py:123-129`: daemon só chama refresh se `ws_codex.is_alive(...) and ws_codex.needs_refresh(...)`.

**Impacto**

Com os defaults atuais, não existe idade que satisfaça simultaneamente `age < 300` e `age >= 300`. O daemon nunca refresca Codex. Resultado: warm Codex expira silenciosamente, cache prefix cai frio e qualquer confiança em `burnless warm daemon` para Codex é falsa.

**Recomendação**

Definir `HEARTBEAT_INTERVAL_S` abaixo de `TTL_S` com headroom real, por exemplo 240s se TTL conservador for 300s. Adicionar teste unitário para `_maybe_refresh()` com estado Codex em `age=240s` que verifica chamada a `ws_codex.refresh()`, e teste de invariantes `HEARTBEAT_INTERVAL_S < TTL_S`.

## Achados Importantes

### 4. Importante — Retry automático é cego para erro determinístico

**Evidência**

- `src/burnless/exec/runner.py:849-868`: qualquer `PART`/`ERR` não interrompido entra em retry se houver tentativa restante, exceto `context_overflow_retry_exhausted`.
- `src/burnless/exec/runner.py:878-882`: retry chama `agents_mod.run()` sem classificar erro.
- `src/burnless/agents.py:315-322`: já existe `_retryable_provider_failure()` que distingue timeout/stale/5xx de falhas determinísticas, mas ela é usada para fallback de provider, não para o retry principal.
- `tests/test_retry_loop.py:103-207`: os testes simulam retry copiando lógica, mas só cobrem PART que vira OK; não há caso de `returncode=2` por parse de CLI, binário ausente, flag inválida, SyntaxError etc.

**Impacto**

Falhas como `unexpected argument`, `argument cannot be used multiple times`, `command not found`, erro de sintaxe em comando ou validação determinística são repetidas de forma idêntica. Isso gasta tempo/quota e polui logs com `[retry] dXXX: prev=ERR` sem chance de recuperação.

**Recomendação**

Criar `is_retryable_run_failure(summary, result)` central. Reusar `_retryable_provider_failure()` para stderr/stdout e incluir allowlist para stale/timeout/5xx/rate limit, evitando retry para `returncode=2`, `usage:`, `unexpected argument`, `command not found`, `SyntaxError`, `ModuleNotFoundError`, `No such file or directory` de binário. Testar via `execute_delegation()` com fake runner retornando rc=2 e garantir `retry_count=0`.

### 5. Importante — Retry usa outro caminho de execução e pode trocar provider/modelo

**Evidência**

- Primeira execução seleciona provider em `src/burnless/exec/runner.py:464-466` (`selected_agent_cfg`).
- Primeira execução subprocess usa `live_runner.run_with_overflow_retries()` em `src/burnless/exec/runner.py:576-595`.
- Retry principal chama `agents_mod.run(agent_cfg, ...)` em `src/burnless/exec/runner.py:878-882`, usando `agent_cfg` original do tier, não `selected_agent_cfg`.
- `agents_mod.run()` re-ranqueia providers em `src/burnless/agents.py:854-886`.
- O retry também não recebe `tier=tier` nessa chamada (`src/burnless/exec/runner.py:880-882`), então `agents_mod.run()` usa `tier_name="default"` em `src/burnless/agents.py:854-857` quando `agent_cfg` não tem `provider_tier`.

**Impacto**

Uma primeira tentativa pode rodar via provider A, falhar com PART/ERR, e o retry rodar provider B por re-ranqueamento ou chave de health diferente. Além disso, o retry não usa o mesmo `live_runner` que acabou de receber correções de flag/warm/live streaming. Isso reabre a classe de bug “função helper correta, fiação real não chama”.

**Recomendação**

Retry deve repetir o mesmo backend e `selected_agent_cfg`, ou registrar explicitamente quando está tentando fallback de provider. Para subprocess, chamar `live_runner.run_with_overflow_retries()` novamente com `selected_agent_cfg`; para ollama/cached_worker/maestro, usar adaptador equivalente. Se quiser autobalance no retry, passar `tier=tier` e registrar como provider fallback, não como retry transparente.

### 6. Importante — `--cold-cache` é documentado para `run`/`do`, mas só funciona no `cached_worker`

**Evidência**

- Help de `run`: `src/burnless/cli.py:1578-1583` promete “inject a nonce into the system block to guarantee a cache miss”.
- Help de `do`: `src/burnless/cli.py:1804-1809` promete “inject a nonce to force cache miss”.
- `src/burnless/exec/runner.py:544-560`: `cold_cache=opts.cold_cache` só é repassado para `cached_worker.run_cached_worker()`.
- `src/burnless/cached_worker.py:362-365`: apenas cached worker chama `bust_cache(system)`.
- `src/burnless/exec/runner.py:576-595` e `src/burnless/live_runner.py:265-410`: subprocess/live runner não recebe nem usa `cold_cache`.

**Impacto**

Benchmarks de cold cache via `burnless run --cold-cache` ou `burnless do --cold-cache` são falsos no backend padrão subprocess (`claude -p`, Codex, Gemini). O operador acha que está medindo cold start, mas warm-session/prefix cache pode continuar ativo.

**Recomendação**

Implementar cold-cache no prompt/contexto antes de qualquer backend, por exemplo adicionando nonce em `_with_runtime_context()` quando `RunOpts.cold_cache` estiver ativo, e desabilitar warm injection para subprocess nessa run. Alternativamente esconder a flag quando `cache_worker.enabled` não está ativo. Adicionar teste que `live_runner` recebe prompt com nonce ou que warm args não são injetados quando `cold_cache=True`.

### 7. Importante — `burnless warm status` está quebrado para status multi-modelo

**Evidência**

- `src/burnless/warm_session.py:422-431`: `status(model=None)` retorna dict por modelo (`{"model": status_dict}`).
- `src/burnless/warm_session_codex.py:405-414`: Codex faz o mesmo.
- `src/burnless/cli.py:849-862`: `cmd_warm_status()` chama `ws.status(bl_root)` sem modelo e testa `s.get("exists")`; em retorno multi-modelo, `exists` não existe, então imprime “NOT INITIALIZED”.
- `src/burnless/cli.py:862-874`: mesmo bug para Codex.
- `cmd_warm_explain()` já trata multi-modelo corretamente em `src/burnless/cli.py:905-923`, evidenciando divergência entre comandos irmãos.

**Impacto**

Depois da refatoração para pools globais por modelo, `burnless warm status` pode dizer que não há warm session mesmo quando existem arquivos em `~/.burnless/warm/<provider>/*.json`. Isso induz refresh/init desnecessário e dificulta diagnóstico.

**Recomendação**

Fazer `cmd_warm_status()` espelhar `cmd_warm_explain()`: se o retorno não tem `exists`, iterar por modelos. Adicionar testes CLI de status com monkeypatch de `ws.status()` retornando `{"m": {"exists": True, ...}}`.

### 8. Importante — TTL/explain Codex contradiz a própria implementação

**Evidência**

- `src/burnless/warm_session_codex.py:32`: `TTL_S = 300`.
- `src/burnless/warm_session_codex.py:456-464`: `explain()` hardcodeia `ttl_min = 60.0` e `aging_threshold = 59.0`.
- `tests/test_warm_explain.py:123-172`: testes codificam a expectativa de 60 minutos para Codex (`age_s=3540` aging, `age_s=3600` expired).
- `src/burnless/warm_session_codex.py:8-13` comenta empiria de TTL >=600s e janela parcial em 126s, o que também não casa com 60 minutos.

**Impacto**

UI/diagnóstico diz “fresh” e “ttl_remaining_min” por quase uma hora, enquanto `is_alive()` considera morto após 5 minutos. Isso causa decisões opostas entre daemon/runner e comando de explain.

**Recomendação**

Definir uma única fonte de verdade (`TTL_S` e `HEARTBEAT_INTERVAL_S`) e derivar `explain()` dela. Corrigir testes para usar os valores reais. Se a intenção mudou para 60 min, então `TTL_S` precisa ser 3600 e o daemon precisa de heartbeat < TTL.

### 9. Importante — `apply_worker_overrides()` descarta campos de tier e providers em vez de sobrepor só provider/modelo

**Evidência**

- `src/burnless/config.py:328-338`: cada override substitui `agents[tier]` inteiro pelo retorno de `build_worker_agent()`.
- `build_worker_agent()` retorna só `name`, `command`, `provider` e, para Ollama, `tools/model` (`src/burnless/config.py:289-325`).
- Campos como `role`, `use_for`, `providers`, `sandbox`, `workspace_root`, `allow_net`, `shell_timeout_s` e metadados locais do tier são perdidos durante o override temporário.

**Impacto**

Mesmo que o arquivo fosse restaurado no fim, a run em si perde customizações que pertencem ao tier. Exemplo: um tier Codex com `workspace_root`, `sandbox=workspace-write` ou `allow_net` não preserva esses campos quando o operador só queria trocar o modelo.

**Recomendação**

Aplicar override como merge em cima do tier existente: preservar metadados seguros (`role`, `use_for`, sandbox/workspace) e substituir apenas provider/model/command conforme necessário. Para `providers`, decidir explicitamente se o override substitui a lista inteira ou injeta provider primário temporário; documentar.

## Achados Menores

### 10. Menor — Delegation criada antes do override registra agente antigo

**Evidência**

- `src/burnless/cli.py:1112`: `cmd_do()` chama `cmd_delegate()` antes de aplicar `_worker_overrides`.
- `src/burnless/cli.py:233-241`: delegation renderiza `agent_name=agent_cfg["name"]` com config original.
- `src/burnless/cli.py:1126-1135`: override só é aplicado depois.

**Impacto**

O Markdown da delegação e o output inicial podem dizer `gold/opus` enquanto a execução real usa `codex:gpt-5.5` ou outro worker. Isso atrapalha auditoria e debug.

**Recomendação**

Com o fix in-memory do achado 2, passar o cfg efetivo também para `cmd_delegate()` ou renderizar explicitamente “requested tier: gold; runtime override: codex:gpt-5.5”.

### 11. Menor — `cmd_warm_init --provider both --model X` aplica o mesmo modelo a Claude e Codex

**Evidência**

- `src/burnless/cli.py:816-834`: `args.model` é usado tanto como modelo Claude quanto como modelo Codex quando provider é `both`.
- Help de `warm init`: `src/burnless/cli.py:1749-1752` só diz “model for warm session (default: claude-sonnet-4-6)” e provider default `both`.

**Impacto**

`burnless warm init --provider both --model gpt-5.5` tenta inicializar Claude com `gpt-5.5`, e `--model claude-sonnet-4-6` tenta inicializar Codex com modelo Claude. O default `both` torna o erro fácil.

**Recomendação**

Separar flags (`--claude-model`, `--codex-model`) ou exigir `--provider` quando `--model` é passado. Ajustar help para refletir defaults por provider.

### 12. Menor — Testes de wiring ainda usam inspeção de source para regressões críticas

**Evidência**

- `tests/test_codex_flag_dedup.py:81-90`: valida que a string `_dedup_valueless_flags(command)` existe no source.
- `tests/test_codex_flag_dedup.py:93-109`: valida ordem de strings no source para checar guard de provider.
- Não há teste que execute `run_with_live_panel()` com fake `Popen`/fake warm module e capture o comando final.

**Impacto**

Esse tipo de teste pode passar mesmo que a chamada esteja morta, dentro de branch errada ou rode depois do ponto que importa. Foi exatamente a classe de falha original: helper correto, fiação real divergente.

**Recomendação**

Adicionar teste comportamental com monkeypatch de `subprocess.Popen`, `shutil.which`, warm module e `resolve_command`, capturando `command` final. Afirmar ausência de flags Claude em Codex, dedup real, modelo correto e posição de flags Codex.

## Observações de Paridade

- Claude tem warm por `--resume <uuid> --fork-session`; Codex tem warm por prefixo + `--cd` isolado. A diferença é legítima, mas hoje há divergências não documentadas em TTL, daemon e model selection.
- Gemini e Ollama têm `warm_module=None` em `src/burnless/coreconfig/schema.py:181-199`; ausência de warm parece estrutural, mas o usuário não recebe aviso no path de execução. Isso é aceitável se documentado.
- O caminho `agents.py::_inject_warm_fork_args()` é mais central e tem defesa de provider mismatch (`src/burnless/agents.py:712-716`) que `live_runner.py` não replica. Mesmo após os fixes de 2026-07-02, `live_runner.py` ainda reimplementa detecção/import/init/warm-prefix em `src/burnless/live_runner.py:327-359`.

## Recomendações de Teste Prioritárias

1. Teste end-to-end sem subprocess real para `cmd_do --tier gold --gold codex:gpt-5.5`: config em disco nunca muda; selected command contém `-m gpt-5.5`; delegation/log indicam override.
2. Teste de retry determinístico: rc=2 + stderr `unexpected argument` não faz retry.
3. Teste de retry preservando provider: primeira tentativa com provider selecionado X; retry também usa X, salvo fallback explícito.
4. Teste de warm daemon Codex: estado vivo perto do heartbeat dispara refresh.
5. Teste de `warm status` multi-modelo para Claude e Codex.

