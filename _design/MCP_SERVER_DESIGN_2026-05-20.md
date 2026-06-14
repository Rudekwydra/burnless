> SUPERSEDED 2026-06-14: chat/pty/maestro-REPL cut from v1 (see cut_chat_pty_2026_06_14.md)

# MCP Server `mcp__burnless__*` — Design + Spec

**Delegation:** d272 (gold)
**Date:** 2026-05-20
**Audience:** Brain Claude Code (or any MCP client) → Burnless tools nativos, sem shell out.
**Status:** design only — no implementation in this doc.

---

## 1. Decisão arquitetural

### 1.1 Transport: **stdio** (default + único suportado em v1)

- MCP padrão é stdio. Claude Code descobre servers via `~/.claude.json` (`mcpServers` block) e spawn por stdio.
- HTTP transport fica **fora de escopo v1**. Justificativa: workers já são subprocess locais; nenhum cenário multi-host justifica HTTP agora. Adicionar depois sem quebrar v1.
- Server roda como processo filho do Brain (Claude Code). 1 server por Brain session. Não há server "long-running" compartilhado entre sessões — cada sessão Claude Code spawna seu próprio.

### 1.2 Entry point: `python -m burnless.mcp_server`

- Novo módulo `src/burnless/mcp_server.py`.
- Empacotado no wheel existente. Sem nova dependência runtime além de `mcp` (SDK Python oficial: `pip install mcp`).
- `pyproject.toml` adiciona optional extra `mcp = ["mcp>=1.0"]`. Default install não pesa.
- Registro no Claude Code: `~/.claude.json` entry → `{"command": "python", "args": ["-m", "burnless.mcp_server"], "env": {}}`. Documentado em `docs/mcp.md`.

### 1.3 Detecção de `.burnless/` do projeto ativo

**Regra dura:** server usa **CWD do processo Brain como ponto de partida** + walk-up tipo `git rev-parse --show-toplevel`. Reutiliza `burnless.paths.find_root()` (já existe — sobe procurando `.burnless/`).

- Cada tool resolve `.burnless/` a cada chamada via `paths.find_root(Path.cwd())`. NÃO cacheia entre calls (CWD pode mudar entre sessões).
- Se `.burnless/` não encontrado → retorna `{"error": "no_burnless_root", "hint": "run `burnless init` in project root"}`. Nunca cria diretório implicitamente.
- Permite override explícito via param opcional `project_root` em cada tool (path absoluto). Útil pra IDE que abre múltiplos projetos.

### 1.4 Workers em background (run async)

**Regra dura:** Brain NÃO mantém estado entre tool calls. Tudo via filesystem.

- `mcp__burnless__run(id, background=False)` (default sync): bloqueia, retorna envelope JSON do worker quando termina. Reutiliza `live_runner.run_with_overflow_retries()`.
- `mcp__burnless__run(id, background=True)`: spawn subprocess detached (`subprocess.Popen` com `start_new_session=True`), grava PID em `.burnless/runs/<id>.pid`, retorna `{"id": "dXXX", "pid": N, "status": "running", "log_path": "..."}`. Brain poll via `mcp__burnless__status(id)`.
- BG run grava stdout em `.burnless/runs/<id>.stdout.log` (append). `status` lê tail + checa PID vivo (`os.kill(pid, 0)`).
- Final envelope JSON do worker fica em `.burnless/runs/<id>.envelope.json` quando worker termina (escrito por wrapper que captura saída do subprocess). Brain lê via `mcp__burnless__capsule(id)` (depois que worker finaliza) ou `mcp__burnless__read(id)` (3-paths fallback existente).
- Sem websocket / streaming. Brain faz polling explícito. Justificativa: MCP tools são request/response; streaming exige notifications/server-sent events que complicam v1. Logs streaming = optional v2.

### 1.5 Registry / discovery

- Server expõe tools via decorator MCP padrão (`@server.list_tools()` / `@server.call_tool()`).
- Schemas dos tools (JSONSchema) gerados de dataclasses Python em `mcp_server.py`. NÃO hand-write JSONSchema duplicado.
- 1 server, 1 lista de tools. Sem sub-servers / namespacing interno. Brain vê: `mcp__burnless__delegate`, `mcp__burnless__run`, `mcp__burnless__capsule`, `mcp__burnless__read`, `mcp__burnless__route`, `mcp__burnless__status`. **6 tools. Não inventar mais.**

### 1.6 Concorrência

- Server é single-threaded asyncio (MCP SDK default). Tool calls são serializadas no server — uma de cada vez.
- BG runs são processos separados; servidor só inicia/audita. Sem race no `.burnless/` dir (cada tool faz read-modify-write atômico ou append-only no `audit.jsonl`).
- Múltiplos BG runs simultâneos OK (já suportado pelo CLI). Server só dispatcha.

### 1.7 Plugins / Plugin Protocol v0.7

**Hooks H1–H8 continuam funcionando.** Server invoca os mesmos code paths do CLI (`delegations.write_delegation`, `live_runner.run_with_overflow_retries`). Plugin loader já lê `~/.burnless/plugins/*.json` no startup. MCP server NÃO altera plugin dispatch.

### 1.8 Compat com CLI existente

- `burnless delegate ...` continua intacto. MCP server é **endpoint paralelo**, não substituto.
- Mesma config (`.burnless/config.yaml`), mesma routing (`burnless.routing.route()`), mesmos capsule paths.
- Server pode ser desligado: sem MCP entry no `~/.claude.json` → Brain volta ao shell wrapper.

### 1.9 Erros e edge cases

- Cada tool retorna dict com `error` field on failure (não raise exception). MCP SDK serializa.
- Tipos de erro padronizados: `no_burnless_root`, `invalid_tier`, `delegation_not_found`, `worker_failed`, `audit_failed`, `config_error`.
- Tool calls com input inválido → erro estruturado, nunca crash do server.

---

## 2. Plano de implementação (Bronze tickets)

Cada bullet = ticket bronze separado. Mantém escopo cirúrgico.

1. **Add `mcp` optional dep.** Editar `pyproject.toml`: `[project.optional-dependencies] mcp = ["mcp>=1.0"]`. Documentar no README seção install.
2. **Criar `src/burnless/mcp_server.py`.** Esqueleto: import `mcp.server.stdio`, `mcp.server.Server`, async `main()`. 6 tools registered como stubs retornando `{"todo": true}`. Entry point `if __name__ == "__main__": asyncio.run(main())`.
3. **Implementar `delegate` tool.** Wrappar `delegations.write_delegation()` + `routing.route()`. Input: `text` (str), `tier` (str|None), `project_root` (str|None). Output: dict `{id, agent, tier, path, routed_by}`.
4. **Implementar `route` tool.** Wrappar `routing.explain_route()`. Input: `text` (str), `project_root` (str|None). Output: dict `{tier, agent, matched_keyword, default_used}`.
5. **Implementar `status` tool.** Dual mode:
   - Sem `id`: project-wide health (capsules count, pending delegations, last run). Wrappar lógica de `burnless status` do CLI.
   - Com `id`: status de uma delegation (running / done / failed). Lê `.burnless/runs/<id>.pid` + envelope.
6. **Implementar `run` tool (sync).** Wrappar `live_runner.run_with_overflow_retries()`. Input: `id` (str), `background` (bool, default False), `project_root` (str|None). Sync mode: bloqueia, retorna envelope JSON parseado.
7. **Implementar `run` BG mode + `runs/` dir lifecycle.** Criar `.burnless/runs/`. BG: spawn detached subprocess de `python -m burnless run <id>`, escrever pid file, stdout log, envelope.json quando worker emite final JSON. Reaproveita parser de `delegations.extract_result_json()`.
8. **Implementar `capsule` tool.** Lê `.burnless/capsules/<id>.json`. Input: `id` (str), `project_root` (str|None). Output: dict (capsule contents raw) ou `{error: "delegation_not_found"}`.
9. **Implementar `read` tool.** Reproduz 3-paths fallback do CLI: capsule → temp envelope → log tail. Input: `id` (str), `project_root` (str|None), `max_log_lines` (int, default 200). Output: dict `{source: "capsule"|"envelope"|"log", content: ...}`.
10. **Documentar.** Criar `docs/mcp.md` com: install, registro em `~/.claude.json`, exemplos de chamada por tool, troubleshooting.
11. **Testes.** `tests/test_mcp_server.py`: 1 test por tool, com `.burnless/` mock em `tmp_path`. Não testar BG run com PID real — mock subprocess.
12. **Smoke E2E manual.** Registrar server em `~/.claude.json` de teste, rodar Claude Code, chamar `mcp__burnless__route("fix typo")` e validar retorno bronze.

Ordem de dispatch: 1 → 2 → (3, 4, 8 em paralelo) → (5, 6) → 7 → 9 → 10 → 11 → 12.

---

## 3. Spec apertada Bronze — pronta pra `burnless delegate --tier bronze`

> Copy/paste literal abaixo para o Brain dispachar como tickets bronze. Cada ticket é independente, com DoD verificável via grep/test.

### 3.1 Tool: `mcp__burnless__delegate`

**Função:** cria delegation file + roteia tier.
**Wrappar:**
- `burnless.paths.find_root()` p/ resolver `.burnless/`.
- `burnless.routing.route(text, routing_rules, default_tier)` p/ tier.
- `burnless.delegations.write_delegation()` p/ persistir.
- Próximo id: ler `.burnless/delegations/` listing, achar maior `dNNN`, incrementar (lógica já no CLI — extrair p/ helper se preciso).

**Input schema:**
```python
{
  "text": str,                    # required, non-empty
  "tier": Optional[str],          # one of: bronze, silver, gold, diamond. None → auto-route
  "project_root": Optional[str],  # abs path; None → use cwd walk-up
}
```

**Output schema (success):**
```python
{
  "id": "d042",
  "tier": "bronze",
  "agent": "claude-haiku-4-5",
  "routed_by": "manual" | "auto-route" | "default",
  "matched_keyword": "implementa" | None,
  "delegation_path": "/abs/path/.burnless/delegations/d042.md",
  "created_at": "2026-05-20T15:00:00+00:00"
}
```

**Output schema (error):**
```python
{"error": "no_burnless_root" | "invalid_tier" | "config_error", "hint": str}
```

**Exemplo:**
```python
mcp__burnless__delegate(text="implementa endpoint /usage", tier="bronze")
# → {"id": "d042", "tier": "bronze", "agent": "claude-haiku-4-5", "routed_by": "manual", "delegation_path": ".../d042.md", "created_at": "..."}
```

**Edge cases:**
- `text=""` → `{"error": "invalid_input", "hint": "text must be non-empty"}`.
- `tier="diamond"` mas config não tem agent diamond → `{"error": "config_error", "hint": "tier 'diamond' not configured in .burnless/config.yaml"}`.
- `project_root` inválido / sem `.burnless/` → `{"error": "no_burnless_root", "hint": "..."}`.

**PROIBIÇÕES DURAS:**
- NÃO rodar o worker. `delegate` SÓ cria o arquivo. `run` é separado.
- NÃO modificar formato do delegation `.md` existente — reaproveita `write_delegation()`.
- NÃO criar `.burnless/` se não existir — retorna erro.

---

### 3.2 Tool: `mcp__burnless__route`

**Função:** preview de tier sem criar delegation.
**Wrappar:** `burnless.routing.explain_route()`.

**Input schema:**
```python
{
  "text": str,                    # required, non-empty
  "project_root": Optional[str],
}
```

**Output schema:**
```python
{
  "tier": "bronze",
  "agent": "claude-haiku-4-5",
  "matched_keyword": "implementa" | None,
  "default_used": bool,
  "routing_rules_snapshot": {...},  # dict from config
}
```

**Exemplo:**
```python
mcp__burnless__route(text="refactor architecture")
# → {"tier": "gold", "agent": "claude-opus-4-7", "matched_keyword": "refactor", "default_used": false, ...}
```

**Edge cases:**
- Empty `text` → `{"error": "invalid_input"}`.
- No `.burnless/` → erro padrão.

**PROIBIÇÕES DURAS:**
- NÃO criar nenhum arquivo. Read-only.
- NÃO chamar LLM. Pure regex routing.

---

### 3.3 Tool: `mcp__burnless__run`

**Função:** executa delegation. Sync (block) ou BG (detached subprocess).

**Input schema:**
```python
{
  "id": str,                      # e.g. "d042"
  "background": bool,             # default False
  "project_root": Optional[str],
}
```

**Output schema (sync, success):**
```python
{
  "id": "d042",
  "status": "OK" | "PART" | "ERR" | "BLK",
  "envelope": {...},              # full worker JSON envelope
  "capsule_path": "/abs/.../d042.json",
  "duration_seconds": 42.3,
}
```

**Output schema (background, success):**
```python
{
  "id": "d042",
  "status": "running",
  "pid": 12345,
  "log_path": "/abs/.../runs/d042.stdout.log",
  "envelope_path": "/abs/.../runs/d042.envelope.json",  # will appear when worker done
}
```

**Output schema (error):**
```python
{"error": "delegation_not_found" | "worker_failed" | "config_error", "hint": str}
```

**Exemplo:**
```python
# Sync:
mcp__burnless__run(id="d042")
# → {"id": "d042", "status": "OK", "envelope": {...}, "capsule_path": "...", "duration_seconds": 38.1}

# BG:
mcp__burnless__run(id="d042", background=True)
# → {"id": "d042", "status": "running", "pid": 87654, "log_path": "...", "envelope_path": "..."}
```

**Edge cases:**
- `id` não existe em `.burnless/delegations/` → `{"error": "delegation_not_found"}`.
- Worker já rodou (capsule existe) → ainda permite re-run? **NÃO em v1.** Retorna `{"error": "already_run", "hint": "use mcp__burnless__capsule to read prior result"}`.
- BG: PID file já existe e processo vivo → `{"error": "already_running", "pid": N}`.
- Sync timeout: usar timeout do CLI atual (não impor novo).

**PROIBIÇÕES DURAS:**
- NÃO mudar formato do envelope JSON do worker.
- NÃO retry automático além do que `run_with_overflow_retries` já faz.
- BG: usar `start_new_session=True` no Popen pra detach (não morrer junto com MCP server).
- Log file = append-only. NÃO truncar.

---

### 3.4 Tool: `mcp__burnless__capsule`

**Função:** lê capsule finalizada.

**Input schema:**
```python
{
  "id": str,
  "project_root": Optional[str],
}
```

**Output schema:**
```python
{
  "id": "d042",
  "capsule": {...},  # exact JSON contents of .burnless/capsules/d042.json
  "path": "/abs/path/...",
}
```

**Output schema (error):** `{"error": "delegation_not_found" | "capsule_not_ready", "hint": str}`.

**Exemplo:**
```python
mcp__burnless__capsule(id="d042")
# → {"id": "d042", "capsule": {"status": "OK", "files": [...], ...}, "path": "..."}
```

**Edge cases:**
- Delegation existe mas worker ainda rodando (sem capsule.json) → `{"error": "capsule_not_ready", "hint": "use status tool"}`.
- Delegation não existe → `delegation_not_found`.

**PROIBIÇÕES DURAS:**
- NÃO modificar capsule. Read-only.
- NÃO inferir status se capsule ausente — só lê arquivo.

---

### 3.5 Tool: `mcp__burnless__read`

**Função:** fallback de leitura — 3 paths (capsule → envelope → log tail).

**Input schema:**
```python
{
  "id": str,
  "project_root": Optional[str],
  "max_log_lines": int,           # default 200
}
```

**Output schema:**
```python
{
  "id": "d042",
  "source": "capsule" | "envelope" | "log",
  "content": dict | str,          # dict for capsule/envelope, str for log
  "path": "/abs/...",
}
```

**Lógica:**
1. Se `.burnless/capsules/<id>.json` existe → return capsule.
2. Senão se `.burnless/runs/<id>.envelope.json` existe → return envelope.
3. Senão se `.burnless/runs/<id>.stdout.log` existe → tail `max_log_lines` lines, return as str.
4. Senão → `{"error": "delegation_not_found"}`.

**Exemplo:**
```python
mcp__burnless__read(id="d042")
# during run → {"source": "log", "content": "... worker stdout tail ...", ...}
# after done → {"source": "capsule", "content": {...}, ...}
```

**PROIBIÇÕES DURAS:**
- NÃO inverter ordem dos 3 paths (capsule sempre vence).
- NÃO retornar conteúdo trunco sem indicar via `truncated: true` no payload.

---

### 3.6 Tool: `mcp__burnless__status`

**Função:** project health OU per-delegation status.

**Input schema:**
```python
{
  "id": Optional[str],            # if None → project-wide
  "project_root": Optional[str],
}
```

**Output schema (project-wide, id=None):**
```python
{
  "project_root": "/abs/...",
  "capsules_count": 271,
  "pending_delegations": ["d272", "d273"],   # delegations without capsule
  "running_now": [{"id": "d272", "pid": 12345, "tier": "gold"}],
  "last_capsule": {"id": "d271", "created_at": "...", "status": "OK"},
  "config": {"brain_adapter": "anthropic", "tiers": [...]},
}
```

**Output schema (per-delegation, id="d042"):**
```python
{
  "id": "d042",
  "state": "not_started" | "running" | "done" | "failed" | "missing",
  "pid": int | None,
  "capsule_status": "OK" | "PART" | "ERR" | "BLK" | None,
  "log_size_bytes": int | None,
  "started_at": "..." | None,
  "finished_at": "..." | None,
}
```

**Lógica state per-delegation:**
- `missing`: nenhum `.burnless/delegations/<id>.md`.
- `not_started`: delegation existe, sem pid file e sem capsule.
- `running`: pid file existe e `os.kill(pid, 0)` OK.
- `done`: capsule existe.
- `failed`: pid file existe mas processo morto e sem capsule.

**Exemplo:**
```python
mcp__burnless__status()
# → project-wide

mcp__burnless__status(id="d272")
# → {"id": "d272", "state": "running", "pid": 12345, ...}
```

**PROIBIÇÕES DURAS:**
- NÃO chamar `ps`/`pgrep` — usar `os.kill(pid, 0)`.
- NÃO modificar nada. Read-only.
- NÃO falhar se pid file órfão — marca `failed`.

---

### 3.7 Server bootstrap (`src/burnless/mcp_server.py`)

**Esqueleto obrigatório:**
```python
# Pseudo-código pra Bronze — implementar com mcp SDK real:
# from mcp.server import Server
# from mcp.server.stdio import stdio_server
# import asyncio
#
# server = Server("burnless")
#
# @server.list_tools()
# async def list_tools(): return [Tool(name=..., inputSchema=..., description=...), ...]
#
# @server.call_tool()
# async def call_tool(name, arguments):
#     handlers = {"delegate": handle_delegate, "run": handle_run, ...}
#     return await handlers[name](**arguments)
#
# async def main():
#     async with stdio_server() as (read, write):
#         await server.run(read, write, server.create_initialization_options())
#
# if __name__ == "__main__":
#     asyncio.run(main())
```

**PROIBIÇÕES DURAS no server bootstrap:**
- NÃO cachear `.burnless/` root entre tool calls. Resolver toda call.
- NÃO logar payloads sensíveis pra stderr (vai pro Claude Code log).
- NÃO importar Anthropic SDK no MCP server (workers já tem). Reduz cold start.
- NÃO crashar se uma tool falhar — retornar dict de erro.

---

### 3.8 DoD (Definition of Done) — auditável grep/test

Pra cada ticket Bronze, o orchestrator (Claude main) audita ponto a ponto:

- [ ] `pyproject.toml` tem `mcp = ["mcp>=1.0"]` em `[project.optional-dependencies]`. `grep 'mcp.*mcp>=' pyproject.toml`.
- [ ] `src/burnless/mcp_server.py` existe. `ls src/burnless/mcp_server.py`.
- [ ] Server expõe EXATAMENTE 6 tools: delegate, run, capsule, read, route, status. `grep -E '"(delegate|run|capsule|read|route|status)"' src/burnless/mcp_server.py | wc -l` ≥ 6.
- [ ] `python -m burnless.mcp_server --help` ou stdio init não crasha. Smoke manual.
- [ ] `tests/test_mcp_server.py` existe e roda. `pytest tests/test_mcp_server.py -v` ≥ 6 testes passing.
- [ ] `docs/mcp.md` existe com exemplo de `~/.claude.json` entry.
- [ ] CLI antigo intacto: `burnless --help`, `burnless route "X"`, `burnless status` continuam funcionando. Sanity.
- [ ] `.burnless/runs/` é criado on-demand pelo `run` BG mode. Não polui project se nunca usado.
- [ ] Worker subprocess continua sendo claude/codex bin — NÃO virou in-process. `grep -E 'subprocess\.(Popen|run)' src/burnless/mcp_server.py src/burnless/live_runner.py` mostra subprocess preservado.
- [ ] Formato capsule JSON inalterado. `diff <(head .burnless/capsules/d001.json) <(head .burnless/capsules/d272.json)` mostra mesma estrutura.

Se UM item do DoD falha → PART → rejeita + re-dispatch cirúrgico.

---

## 4. Notas finais

- **Cobertura de tools v1 = 6.** Resistir tentação de adicionar `metrics`, `chain`, `lessons`, `chat` nessa primeira iteração. Roberto pediu "lista mínima viável". `metrics` pode entrar v1.1 trivialmente (wrappa `burnless metrics`); `chain` requer design separado (chained delegations / pipelines).
- **Sem dependência em servidor remoto.** Tudo local. Sem auth, sem TLS, sem nada de rede.
- **Migration path:** Brain pode usar shell wrapper E MCP server simultaneamente. Mesma `.burnless/`, mesma config. Permite A/B comparison.
- **Versionamento:** server reporta versão no MCP init handshake (igual ao `burnless --version`). Brain pode condicionar uso baseado em versão.

Fim do design. Pronto pra Brain copiar a seção 3 (spec apertada) e despachar tickets bronze 1–12 da seção 2.
