# Plano — auto syntax-check no runner (py_compile / bash -n) — 2026-07-02

## Problema

O worker bronze local (gemma-4) reescreve arquivo inteiro em edições e
ocasionalmente corrompe áreas não pedidas (3 incidentes em 2026-07-02, ver
[[bronze-gemma4-fullfile-rewrite-corruption-2026-07-02]]). O nudge no
SYSTEM_PROMPT (d787) pede pro gemma rodar `py_compile`/`bash -n` sozinho,
mas isso depende do modelo obedecer — modelo pequeno ignora instrução às
vezes. A garantia real tem que estar no **runner do Burnless**, fora do
controle do worker: se um arquivo tocado não compila, o runner rebaixa
OK→PART automaticamente, do mesmo jeito que o `## Verify` gate já faz.

## Por que é barato e inteligente

- Reusa exatamente o mecanismo que já existe: `_apply_verify_gate` já
  roda comandos shell pós-worker e já sabe rebaixar OK→PART sem nunca
  promover. A gente só adiciona um gate irmão que gera os comandos de
  syntax-check automaticamente a partir de `summary["files_touched"]`.
- Zero custo de token (é subprocess local, não LLM).
- Vale pra TODO worker, não só gemma — pega corrupção de qualquer tier.
- Não depende do autor da spec lembrar de escrever o check: é automático
  por extensão de arquivo.

## Arquitetura (fatos do código, já li)

Arquivo: `/Users/roberto/antigravity/burnless/src/burnless/exec/runner.py`

- `_apply_verify_gate(summary, verify_cmds, *, cwd, did, log_path, timeout)`
  (linha 286): re-executa os checks do `## Verify`, rebaixa OK→PART no
  primeiro que falha, appenda `verify: N/N checks passed` em
  `summary["validated"]`. É o padrão a espelhar.
- `summary["files_touched"]` (populado pelo envelope do worker; no
  `ollama_tool_worker.py` hoje só registra em `escrever_arquivo` — ver
  ADENDO abaixo) está disponível no runner (usado em linha 400, 432, 1096).
- Call site A do verify gate: linha 891, logo depois do bloco de
  bronze-rescue, antes do retry loop PART/ERR (linha 900). O novo gate
  deve rodar AQUI, ANTES do `_apply_verify_gate`, pra que uma corrupção
  de sintaxe já entre no fluxo de retry existente.

## Peça A — novo gate `_apply_syntax_gate`

### Decisão

Adicionar função `_apply_syntax_gate(summary, *, cwd, did, log_path, timeout)`
em `runner.py`, logo antes de `_apply_verify_gate` (linha 286).

Comportamento:

- No-op se `summary.get("status") != "OK"` (não mexe em quem já é PART/ERR).
- No-op se `files_touched` vazio.
- Para cada path em `summary["files_touched"]`:
  - resolve o path relativo a `cwd` se não for absoluto.
  - se não existe no disco, pula (worker pode ter reportado delete).
  - `.py`  → comando `python3 -m py_compile <path>`
  - `.sh` / `.bash` → comando `bash -n <path>`
  - `.json` → comando `python3 -c "import json,sys; json.load(open(sys.argv[1]))" <path>`
  - outras extensões → sem check (skip).
- Roda cada comando via subprocess (shell=True, cwd, timeout, capture).
- Primeiro que falhar (rc != 0): rebaixa `summary["status"]="PART"`,
  appenda em `issues` `syntax_failed: <cmd> (rc=N): <tail>`, seta
  `summary["next"]=cmd`, escreve log, retorna. (Mesma forma exata do
  `_apply_verify_gate`.)
- Se todos passam: appenda em `summary["validated"]`
  `syntax: K/K files ok` e retorna.
- NUNCA promove status; só pode rebaixar OK→PART.

### Wiring

Em `runner.py` linha ~890, ANTES do bloco `# ── Honest exit code gate
(call site A)`:

```python
    # ── Auto syntax gate (catches worker file corruption regardless of tier) ──
    if cfg.get("validation", {}).get("auto_syntax_check", True):
        summary = _apply_syntax_gate(
            summary, cwd=root.parent, did=did, log_path=log_path,
            timeout=cfg.get("validation", {}).get("verify_timeout_s", 120),
        )
```

Fazer o MESMO no call site B (linha ~984, o segundo `_apply_verify_gate`
usado no fluxo de retry) pra cobrir também as saídas de retry.

### Config

Novo flag em `validation`: `auto_syntax_check` (default `True`). Documentar
em `docs/` onde os outros flags de `validation` estão descritos (mesma
seção de `honest_exit_code`, `preflight_verify`, `verify_timeout_s`).

## Peça B (ADENDO obrigatório) — corrigir files_touched pra cobrir substituir_trecho

### Problema

`/Users/roberto/antigravity/burnless/.burnless/ollama_tool_worker.py` só
adiciona a `files_touched` quando a tool usada é `escrever_arquivo`
(linha ~226: `if name == "escrever_arquivo" and result.startswith("OK:")`).
Agora que o worker vai preferir `substituir_trecho` (d786/d787), edições
por substituição NÃO entram em `files_touched` — e o auto-syntax-gate,
que depende de `files_touched`, não veria esses arquivos. O gate ficaria
cego justamente pro caminho novo.

### Decisão

No `ollama_tool_worker.py`, estender o registro de side-effect: registrar
o path em `files_touched` quando a tool for `escrever_arquivo` OU
`substituir_trecho` e o result começar com `OK`. (O path do
`substituir_trecho` está em `args["caminho"]`, mesmo campo.)

Isso também conserta o bug menor que anotei antes: o resumo da delegação
reportando "0 arquivos" quando o worker só usou `substituir_trecho`.

## Peça C — testes

Arquivo novo: `/Users/roberto/antigravity/burnless/tests/test_auto_syntax_gate.py`

Casos:
1. `_apply_syntax_gate` rebaixa OK→PART quando um `.py` em files_touched
   tem sintaxe inválida.
2. mantém OK e appenda `syntax: N/N files ok` quando todos válidos.
3. no-op quando status já é PART (não "conserta" pra OK).
4. no-op quando files_touched vazio.
5. `.sh` inválido (ex: `if [ ` sem `fi`) → PART via `bash -n`.
6. `.json` malformado → PART.
7. extensão desconhecida (`.md`) → ignorada, status intacto.
8. path em files_touched que não existe no disco → pulado sem erro.

Para files_touched no ollama_worker: teste que simula um tool_call
`substituir_trecho` com result `OK:` e confirma que o path entra em
`files_touched` (pode ser teste de unidade da lógica de side-effect, sem
subir ollama de verdade).

## Ordem de execução recomendada

1. Peça B (ADENDO) — bronze ou silver, cirúrgico, no ollama_tool_worker.py.
   (Precisa vir antes ou junto, senão o gate nasce cego pra substituir_trecho.)
2. Peça A — silver, no runner.py (arquivo core, spec apertada, PROIBIÇÕES).
3. Peça C — silver, testes; roda `pytest` no Verify.

## PROIBIÇÕES DURAS (pra quando virar spec)

- NÃO alterar a assinatura nem o comportamento de `_apply_verify_gate`.
- NÃO permitir que o novo gate PROMOVA status (só rebaixa OK→PART).
- NÃO rodar syntax-check em arquivo que não existe (worker pode deletar).
- NÃO quebrar o fluxo se `files_touched` tiver path fora de `cwd` —
  resolve absoluto e checa existência antes.
- NÃO tornar o gate obrigatório sem flag de config com default seguro.
- Paths absolutos em toda a spec e no `## Verify` (require_absolute_paths).

## Verify (esboço pra spec futura)

```sh
cd /Users/roberto/antigravity/burnless
python3 -m pytest tests/test_auto_syntax_gate.py -q
python3 -m py_compile src/burnless/exec/runner.py
grep -q 'def _apply_syntax_gate' /Users/roberto/antigravity/burnless/src/burnless/exec/runner.py
grep -q 'auto_syntax_check' /Users/roberto/antigravity/burnless/src/burnless/exec/runner.py
python3 -m py_compile /Users/roberto/antigravity/burnless/.burnless/ollama_tool_worker.py
```

## Custo/risco

- Peça A e B: baixo risco, bem localizadas. A única sensibilidade é
  runner.py ser core compartilhado — spec apertada + pytest cobrindo os 8
  casos protege.
- Impacto em performance: +1 subprocess rápido por arquivo tocado, só
  quando status=OK. Desprezível.
- Ganho: qualquer corrupção de sintaxe por qualquer worker vira PART
  automático e entra no retry loop existente — sem depender de o autor da
  spec lembrar de escrever o check nem de o modelo obedecer o nudge.
