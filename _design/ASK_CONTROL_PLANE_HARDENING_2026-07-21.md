# Plano apertado — `burnless ask` como control plane Gold/Diamond

Data: 2026-07-21  
Status: proposta para execução  
Origem: dogfooding no `rw-editorial-engine`

## Decisão

`burnless ask` passa a ser o caminho obrigatório para trabalho **somente cognitivo** em Gold e Diamond: entendimento, planejamento, arquitetura, crítica, arbitragem e segunda opinião.

| Tipo de trabalho | Caminho canônico |
|---|---|
| Ler, classificar, resumir | `burnless ask --tier bronze` |
| Implementar, testar, editar ou usar ferramentas | `burnless do/run --tier silver` |
| Planejar, arquitetar, julgar ou arbitrar texto | `burnless ask --tier gold` |
| Decisão rara, irreversível ou segunda opinião final | `burnless ask --tier diamond` |

Invocação direta de modelo Gold/Diamond vira escape diagnóstico explícito, com motivo e auditoria. Diamond nunca entra em loop automático, retry editorial ou votação por volume.

## Regra operacional a documentar

> Modelos caros compram julgamento, não mão de obra. Se a saída necessária é apenas texto, use `ask`. Se a tarefa exige ferramentas ou artefatos, use `do/run`.

Atualizar primeiro:

- `docs/DOCTRINE.md`
- `docs/USING_BURNLESS_FROM_YOUR_LLM.md`
- `docs/COMMANDS.md`
- `BURNLESS_FOR_LLMS.md`
- bloco gerenciado do `~/.claude/CLAUDE.md`
- template Codex definido no plano irmão

## Ajustes 1–7

### 1. Envelope e telemetria nativos de `ask`

Criar o contrato `burnless.ask/v1` e eventos `ask.started`, `ask.routed`, `ask.completed` e `ask.failed`.

Campos mínimos:

- `request_id`, horário, projeto e origem;
- tier pedido/efetivo, provider, modelo e esforço;
- motivo e fonte da rota;
- tokens de entrada, saída, cache e total;
- uso exato versus estimado, custo e latência;
- orçamento, warnings, exit code e hash do prefixo.

CLI:

- modo texto continua imprimindo só a resposta;
- `--output-format json` retorna envelope normalizado;
- segredos, prompts integrais e variáveis de ambiente nunca entram no ledger.

Arquivos: `pure_ask.py`, `cli.py`, `events.py`, `usage_meter.py`; testes em `test_pure_ask.py` e novo `test_ask_events.py`.

### 2. Uma única contabilidade

Fazer `status`, `metrics` e `economy` consumirem o mesmo ledger e a mesma janela temporal. Eliminar fórmulas paralelas.

Regras:

- totais observados prevalecem sobre estimativas;
- toda estimativa é rotulada;
- chamadas `ask` aparecem nas três superfícies;
- migração de contadores antigos é idempotente;
- diferenças entre relatórios falham em teste de reconciliação.

Arquivos: `metrics.py`, `economy.py`, `state.py`, `events.py`, `pricing.py`; novo `test_accounting_reconciliation.py`.

### 3. `ask --explain` e `--dry-run`

Antes de gastar, mostrar a decisão efetiva:

- tier, provider, modelo e esforço;
- configuração vencedora e sua origem;
- capacidades disponíveis;
- estimativa de tokens/custo;
- orçamento e política aplicados;
- cache key/prefix hash;
- comando redigido, sem credenciais.

`--dry-run` não chama provider. `--explain` pode acompanhar uma execução real. Ambos usam exatamente o mesmo resolver da chamada, sem reimplementar a rota.

Arquivos: `pure_ask.py`, `coreconfig/resolver.py`, `cli.py`; novo `test_ask_explain.py`.

### 4. Orçamento e capacidades antes da chamada

Adicionar:

- `--max-input-tokens`;
- `--max-output-tokens`;
- `--max-total-tokens`;
- política `hard|soft` por limite;
- capability registry por provider/modelo.

O preflight bloqueia apenas impossibilidades conhecidas. Desvio descoberto após a resposta gera `audit_warning`; a saída é preservada e não há retry automático.

Arquivos: `pure_ask.py`, `estimator.py`, `coreconfig/schema.py` e novo `capabilities.py`; testes de orçamento por adapter.

### 5. Roteador por risco e função, não só palavra

O roteador combina:

- função: leitura, criação, implementação, arquitetura ou auditoria;
- impacto: reversível, publicável, produção ou irreversível;
- pureza: texto apenas ou ferramentas;
- incerteza, novidade e necessidade de segunda opinião;
- política do projeto e override explícito.

Exemplo obrigatório de regressão: “classificar esta copy para publicação” pode ser Gold mesmo contendo “classificar”. Diamond continua opt-in explícito.

Saída da rota: tier, confiança, sinais usados e política vencedora. Arquivos: `routing.py`, `intents.py`, `config.py`; testes table-driven em `test_routing_policy.py`.

### 6. Estado e lifecycle confiáveis

Separar chains em `active`, `stale` e `dead`, usando PID, heartbeat e TTL. O status normal esconde chains mortas e oferece `--all` para auditoria.

O campo `Next` passa a carregar `plan_id`/`revision`; é invalidado quando a etapa é concluída ou o plano muda. Adicionar `burnless gc --dry-run` e GC seguro/idempotente.

Arquivos: `state.py`, `liveness.py`, `lifetime.py`, `cli.py`; ampliar `test_chain_migration_gc.py` e `test_status_surfaces.py`.

### 7. Prefixo estável e cache observável em chamadas stateless

Adicionar `--prefix-file` e `--cache-key`. O prefixo contém doutrina/rubrica estável; o payload variável fica separado. Não criar transcript ou sessão implícita.

Telemetria mínima: hash, bytes/tokens, cache read/write/hit/miss e economia observada. Validar suporte real por adapter; onde não houver cache explícito, registrar `unsupported`, não simular ganho.

Arquivos: `pure_ask.py`, `cache_policy.py`, `cache_modes/*`, `CACHE_PREFIX.md`; novo `test_ask_prefix_cache.py`.

## Ordem de entrega

### P0 — doutrina e confiança

1. Publicar a regra Gold/Diamond e a matriz `ask` versus `do/run`.
2. Entregar envelope, `--dry-run`, `--explain` e orçamento.
3. Unificar a contabilidade e fechar reconciliação.

### P1 — decisão e operação

4. Trocar roteamento por palavras pelo roteamento de risco/função.
5. Corrigir lifecycle e `Next` stale.
6. Entregar prefix cache por adapter.

### P2 — adoção

7. Atualizar setup, templates Claude/Codex e exemplos.
8. Rodar benchmark comparando chamada direta versus `ask`, com pelo menos três repetições por cenário.

## Gates de conclusão

- Uma chamada Gold de texto sem `ask` gera bloqueio ou bypass auditado.
- Uma chamada Diamond exige tier explícito e nunca é repetida automaticamente.
- `ask --dry-run` prevê a mesma rota da execução real.
- `status`, `metrics` e `economy` reconciliam exatamente na mesma janela.
- Exceder orçamento após resposta preserva a saída e gera warning.
- Nenhum ledger ou `--explain` expõe segredo ou prompt integral.
- Testes unitários, integração por adapter e benchmark passam em CI.

## Não objetivos

- Escolher “o melhor modelo” de forma permanente.
- Transformar `ask` em agente com ferramentas.
- Criar votação automática de modelos caros.
- Prometer economia de cache sem evidência observada.
