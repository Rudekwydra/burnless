# Handoff — Burnless v0.7.4 (estado real, 2026-05-11 revisado)

**Para:** Sonnet (próxima sessão em `/Users/roberto/antigravity/burnless`)  
**Autorização Roberto:** "faz plano e delegamos sonnet"  
**ATENÇÃO:** este handoff substitui a versão anterior — profiles.py e cache_policy.py já existem.

---

## TL;DR — estado atualizado (2026-05-11 18h)

| Item | Estado |
|---|---|
| Track A — keepalive subscription (OAuth, 270s, httpx bearer) | ✅ DONE |
| Track B — profiles multi-terminal + 13 testes + `--profile/-p` CLI | ✅ DONE |
| Config fix — Sonnet silver + Haiku bronze como providers | ✅ DONE |
| Track C — keepalive no Desktop Tauri + `cache_policy.py` wiring + `telemetry.py` | ❌ pendente |
| Track D — testes adicionais (keepalive_subscription spec) | ⚠️ parcial |
| Track E — CHANGELOG + README + PyPI tag v0.7.4 | ❌ pendente |

---

## Track A — Keepalive subscription (prioridade 1, ~3-4h)

### Arquivo: `src/burnless/keepalive.py`

**Problema em `keepalive_enabled_by_default()` (linhas 21-26):**
```python
# ATUAL — só ativa com API key:
def keepalive_enabled_by_default(adapter: BrainAdapter | None) -> bool:
    if adapter is None:
        return False
    if adapter.kind != "anthropic":
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
```

**Fix:**
```python
def keepalive_enabled_by_default(adapter: BrainAdapter | None) -> bool:
    if adapter is None or adapter.kind != "anthropic":
        return False
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    # Subscription mode: ativa se OAuth token disponível no Keychain
    from .chat_mode import _load_claude_oauth_token
    return _load_claude_oauth_token() is not None
```

**Adicionar em `KeepaliveDaemon.__init__`:**
- Novo atributo `self._mode: Literal["api_key", "subscription"]`
- Se `os.environ.get("ANTHROPIC_API_KEY")` → `"api_key"`, threshold 3000s (atual)
- Senão OAuth disponível → `"subscription"`, threshold **270s** (4:30 — abaixo do TTL ~5min)

**Mudança em `_send_ping()` para subscription mode:**
- API key mode: comportamento atual (mantém)
- Subscription mode: `httpx` com header `Authorization: Bearer <oauth_token>` + `anthropic-version: 2023-06-01`
- OAuth token via `_load_claude_oauth_token()` que já existe em `chat_mode.py:401`

### Onde está o OAuth token (já implementado):

```python
# src/burnless/chat_mode.py:401
def _load_claude_oauth_token() -> str | None:
    r = subprocess.run(
        ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
        ...
    )
    data = json.loads(r.stdout.strip())
    return data.get("claudeAiOauth", {}).get("accessToken")
```

### Campos a adicionar em `src/burnless/state.py`:
- `keepalive_mode: "api_key" | "subscription"` — para debug
- `keepalive_ttl_window_s: int` — TTL configurado (3000 ou 270)

### Critério de aceite:
`unset ANTHROPIC_API_KEY && burnless chat` → idle 8 min → nova mensagem → `state.json` mostra `keepalive_last_status="ok"` e `cache_read_input_tokens > 0`.

---

## Track B — CLI wiring de profiles (paralelo com A, ~2h)

### Arquivo: `src/burnless/cli.py`

`profiles.py` já existe e tem a lógica toda. Só falta expor no CLI:

1. **Flag global `--profile/-p NAME`** — passar pra `resolve_profile(name)` antes de carregar config. Chamar `get_state_path(profile)` para state isolation.

2. **Env var `BURNLESS_PROFILE`** — fallback se `--profile` não passado: `os.environ.get("BURNLESS_PROFILE")`.

3. **Subcomandos `burnless profile`:**
   ```
   burnless profile list     → list_profiles()
   burnless profile init     → init_profile(name, template)
   burnless profile show     → mostra config resolved do profile ativo
   ```

Referência: `src/burnless/profiles.py:95-130` — tudo já está implementado, só expor.

---

## Track C — Wiring de cache_policy (depois de A+B, ~1h)

### Arquivo: `src/burnless/cached_worker.py`

`cache_policy.py` já existe com `should_compact()`. Integrar no fim de cada turn:

```python
from .cache_policy import should_compact, CompactionDecision

# No fim de _run_worker_turn():
decision = should_compact(
    old_tokens=state.context_tokens,
    compacted_tokens=state.compacted_tokens,
    expected_future_turns=5,  # heuristic
)
if decision.should_compact:
    self._trigger_recompaction()
```

Flag `--recompact=auto|never|always` em `cli.py` que seta `state.recompact_policy`.

---

## Track D — Testes (~2h)

Criar `tests/test_keepalive_subscription.py`:
```python
def test_keepalive_enabled_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("burnless.keepalive._load_claude_oauth_token", lambda: "fake-token")
    adapter = MockAdapter(kind="anthropic")
    assert keepalive_enabled_by_default(adapter) is True

def test_keepalive_subscription_mode_sets_270s_threshold():
    # verificar que KeepaliveDaemon usa 270s em subscription mode
    ...
```

Criar `tests/test_profiles.py`:
```python
def test_resolve_profile_default():
def test_resolve_profile_with_extends():
def test_list_profiles_empty():
def test_init_profile_creates_file():
def test_state_path_isolation():
```

---

## Track E — Docs (~1h)

- `CHANGELOG.md`: seção v0.7.4 com subscription support + profiles + ROI recompaction
- `README.md`: seção "Subscription users (Claude Max/Pro)" + seção "Profiles"
- `examples/profiles/` com 3 yamls exemplo: `claude-sub.yaml`, `codex.yaml`, `ollama.yaml`

---

## Ordem de ataque

```
Track A (keepalive sub) + Track B (cli profiles) → paralelo, independentes
Track C (cache_policy wiring)                    → depois de B (toca cached_worker)
Track D (testes)                                 → depois de A+B+C
Track E (docs)                                   → depois de D
```

---

## Definition of Done v0.7.4

1. `unset ANTHROPIC_API_KEY && burnless chat` → subscription keepalive ativo (mode=270s)
2. `burnless -p ollama do "..."` funciona side-by-side com `burnless -p claude do "..."`
3. `--recompact=auto` ativo por default, logs mostram `CompactionDecision` no fim de turn
4. `pytest tests/` verde incluindo `test_keepalive_subscription.py` + `test_profiles.py`
5. `CHANGELOG.md` e `README.md` atualizados
6. `./scripts/public_git_check.sh` passa
7. Tag `v0.7.4` + PyPI publish + GitHub release

---

## Regras operacionais (CLAUDE.md do antigravity)

- Implementação via `burnless delegate --tier silver` + `burnless run`, nunca Edit/Write direto
- `forgetless rank` antes de cada item
- `codex exec` direto → bloqueado por burnless_guard.sh
- Antes de push: `./scripts/public_git_check.sh`

---

## Artefatos relacionados

- `_design/profiles_spec.md` — spec original profiles
- `_design/subscription_support_plan.md` — plano original (parcialmente desatualizado)
- `_design/brecha7_keepalive_spec.md` — spec keepalive original
- `MATH.md §4` — fórmula ROI recompaction (já implementada em cache_policy.py)
- `_pro/DESKTOP_APP_PLAN.md` — plano desktop (não é escopo do v0.7.4 MIT)

**Quando começar:** ler este arquivo → `forgetless rank "keepalive subscription"` → `forgetless rank "profiles burnless"` → ataca Track A + B em paralelo.
