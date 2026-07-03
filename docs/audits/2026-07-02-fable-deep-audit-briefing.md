# Briefing — segunda auditoria profunda do Burnless (Fable / diamond)

## Papel

Você é a SEGUNDA opinião adversarial e independente sobre o próprio framework Burnless — o
orquestrador multi-tier que está te despachando agora mesmo. Antes de você, o Codex GPT-5.5 já
fez uma primeira auditoria profunda hoje (2026-07-02) e encontrou 12 problemas reais (3 críticos,
6 importantes, 3 menores), TODOS já corrigidos e testados (pytest: 912 passed, 3 falhas
pré-existentes não relacionadas em `test_ollama_worker.py`).

Leia o resultado da primeira auditoria ANTES de começar, para não repetir achados:
`/Users/roberto/antigravity/burnless/docs/audits/2026-07-02-codex-deep-audit-RESULTADO.md`

Resumo dos 12 achados do Codex (todos corrigidos — NÃO os relate de novo como achados seus):
1. `--gold/--silver/... codex:MODEL` não passava `-m MODEL` pro comando codex → corrigido em
   `config.py::build_worker_agent()`.
2. Override "só para esta run" (`--diamond/--gold/--silver/--bronze`) escrevia em
   `.burnless/config.yaml` de verdade e só restaurava no `finally` → agora é 100% em memória
   (`RunOpts.worker_overrides`), nunca toca o arquivo.
3. Warm daemon do Codex nunca refrescava (`TTL_S == HEARTBEAT_INTERVAL_S == 300`, mutuamente
   exclusivos) → `HEARTBEAT_INTERVAL_S` agora é 84 (< TTL_S).
4. Retry automático (PART/ERR) repetia falhas determinísticas (CLI mal formado, binário ausente)
   de forma cega → novo classificador `runner._is_retryable_run_failure()`.
5. Retry usava `agent_cfg` bruto (não o `selected_agent_cfg` da 1ª tentativa) e não passava
   `tier=`, podendo trocar de provider silenciosamente → corrigido, retry agora reusa o provider
   selecionado.
6. `--cold-cache` só funcionava no `cached_worker`; path subprocess (claude/codex/gemini) ficava
   sempre morno → agora busta o prefixo também nesse path.
7. `burnless warm status` sempre dizia "NOT INITIALIZED" pro shape multi-modelo → corrigido,
   espelha `cmd_warm_explain()`.
8. `explain()` do warm Codex hardcodeava TTL de 60min, contradizendo o `TTL_S` real (5min) →
   agora deriva de `TTL_S`/`HEARTBEAT_INTERVAL_S`.
9. `apply_worker_overrides()` substituía o tier inteiro, descartando `role`/`sandbox`/
   `workspace_root`/`allow_net`/`shell_timeout_s` → agora faz merge, só troca provider/model.
10. Delegation markdown renderizava o agente PRÉ-override → `cmd_delegate()` agora aceita
    `cfg_override` e renderiza o agente efetivo.
11. `warm init --provider both --model X` aplicava o MESMO modelo pra claude e codex → agora
    exige `--claude-model`/`--codex-model` com `--provider both`, ou rejeita com erro claro.
12. Testes fracos que só verificavam presença de string no source, não o comportamento real —
    endereçado caso a caso com testes comportamentais novos nas áreas tocadas.

## O que fazer

Você tem DOIS mandatos, nessa ordem de prioridade:

### 1. Auditoria adversarial das áreas AINDA NÃO cobertas pela primeira rodada

O Codex focou só no caminho de execução: `agents.py`, `live_runner.py`, `cli.py`, `config.py`,
`exec/runner.py`, `routing.py`, `spec_validator.py`, `warm_session.py`, `warm_session_codex.py`.

Leia o código real em `/Users/roberto/antigravity/burnless/src/burnless/` e foque em arquivos
que NINGUÉM ainda auditou hoje, por exemplo (lista não-exaustiva, use como ponto de partida):
- `maestro_adapters.py`, `maestro_legacy.py` — camada Maestro
- `cached_worker.py` — backend de cache prompt (Anthropic API direta)
- `epochs.py`, `epochs_v2.py` — rolling memory / carry-forward entre delegações
- `owner_loop.py`, `owner_validate.py`, `owner_cache.py` — ciclo de refinamento do "owner"
- `audit_graph.py`, `audit_stats.py` — grafo/estatísticas de auditoria
- `decisions.py` — cache de decisões prévias injetadas em prompts
- `compression.py`, `contextgc.py` — compressão/coleta de lixo de contexto
- `integrity.py` — snapshot-diff do working tree
- `plugin_loader.py`, `mcp_server.py` — protocolo de plugin / servidor MCP
- `economy.py`, `pricing.py`, `savings_footer.py` — cálculo de custo/economia
- `keepalive.py` — daemon de keepalive de cache
- `profiles.py` — perfis multi-terminal
- `retrieve.py`, `cache_policy.py`, `dashboard.py`
- `coreconfig/` (schema, resolver) e `exec/` (o que sobrou fora do que o Codex já cobriu)

Ataque por estes ângulos (os mesmos que renderam os 12 achados do Codex, mas em código novo):
- **Contratos documentados vs implementação real**: qualquer flag/comportamento que o `--help`
  ou docstring promete mas o código não cumpre.
- **Duplicação de lógica entre arquivos**: mesma responsabilidade implementada em mais de um
  lugar, risco de divergir quando um for corrigido e o outro não.
- **Efeitos colaterais em disco não documentados**: qualquer coisa que deveria ser "só nesta run"
  ou "read-only" mas grava/muda estado persistente.
- **Falhas determinísticas tratadas como transitórias** (ou vice-versa): retry, fallback, cache
  que não distinguem os dois casos.
- **Testes que cobrem a função isolada mas não a fiação real** (helper certo, chamada real
  ausente/errada/em branch morta).
- **Qualquer outro bug, inconsistência ou débito técnico real** que você achar navegando.

### 2. Revisão cética dos 12 fixes que acabaram de ser aplicados

Como segunda opinião, releia os 5 arquivos que o Codex encontrou problema e que foram
corrigidos: `config.py`, `cli.py`, `warm_session_codex.py`, `exec/runner.py`, `live_runner.py`.
Não repita os 12 achados originais — em vez disso, procure por: os fixes introduziram algum bug
NOVO, algum edge case não coberto, alguma inconsistência entre o fix e o resto do código que o
Codex (e quem aplicou os fixes) não viu? Trate isso como uma auditoria adversarial do PATCH, não
uma repetição da auditoria original.

## Formato de saída — grave em disco, não despeje tudo no chat

Escreva a auditoria completa em:
`/Users/roberto/antigravity/burnless/docs/audits/2026-07-02-fable-deep-audit-RESULTADO.md`

Estrutura sugerida: lista de achados, cada um com severidade (crítico/importante/menor), evidência
(arquivo + linha), e recomendação concreta de fix. Ordene por severidade. Separe claramente achados
do mandato 1 (áreas novas) dos achados do mandato 2 (revisão dos fixes), se houver de ambos.

Na resposta final ao Maestro, retorne APENAS:
- Confirmação do path acima
- Quantos achados por severidade (ex: "2 críticos, 4 importantes, 3 menores")
- Os 3 achados mais críticos, em 1 linha cada

## PROIBIÇÕES DURAS

- NÃO edite nenhum arquivo de código do Burnless — isto é auditoria read-only em Markdown.
- NÃO rode `burnless do`/`burnless delegate`/qualquer comando que dispare uma NOVA delegação —
  você pode rodar `pytest`, `grep`, `git log`, leitura de arquivos, mas não invocar o próprio CLI
  do burnless recursivamente.
- NÃO repita os 12 achados do Codex (listados acima) como se fossem novos — cite-os só como
  contexto se relevante, mas não os liste como "achado" na sua auditoria.
- NÃO despeje a auditoria completa na saída do worker — só no arquivo RESULTADO.

## Verify
```sh
test -f /Users/roberto/antigravity/burnless/docs/audits/2026-07-02-fable-deep-audit-RESULTADO.md
grep -qi "crítico\|critico\|importante\|menor" /Users/roberto/antigravity/burnless/docs/audits/2026-07-02-fable-deep-audit-RESULTADO.md
```
