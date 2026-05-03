# v0.4 — Landing burnless.pro + Friendly Mode

Briefing para execução via Burnless Maestro. Ler este documento antes de iniciar.

## Contexto

A landing em `site/index.html` foi atualizada em 02/05 para headline O(N²)→O(N) e remoção do Stripe Pro.
Dois feedbacks independentes (americano externo + Gemini) identificaram os mesmos 4 gaps.
Estes 4 gaps serão fechados nesta release junto com friendly mode porque compartilham o mesmo redeploy.

## Decisões já tomadas (não reabrir)

- Stripe Pro: removido, não volta. Cloud como futuro separado.
- PetAHuman: não vincular ao Burnless nesta fase.
- Counter animado: mantém, não é auditável, é social proof — decisão estratégica.
- Co-autoria de LLM nos commits: não colocar.

---

## Frente 1 — Friendly Mode (produto)

### O que é
Modo de output que expande capsules de volta para linguagem natural.
Hoje o Brain responde em formato capsule (robótico). Friendly on = Haiku expande a resposta mantendo a voz do usuário.

### Impacto nos 3 modos de compressão
- `light`: minifier only + anchor preservado + **friendly on** (Haiku expande)
- `balanced` (default): minifier + encoder + **friendly on**
- `extreme`: todas as camadas + **friendly off** (output robótico puro)

### Onde implementar
- `src/burnless/codec/decoder.py` — adicionar expansor Haiku
- `src/burnless/maestro/brain.py` — passar flag friendly ao decoder
- `src/burnless/config.py` — ler `compression.friendly: true/false`
- Config default: `compression.friendly: true` (muda junto com mode)

### Config resultante
```yaml
compression:
  mode: balanced   # light | balanced | extreme
  friendly: true   # true = Haiku expande output | false = capsule puro
```

---

## Frente 2 — Landing site/index.html

### Gap 1 — Gráfico visual (ALTA prioridade)
O arquivo `docs/cost_chart.png` existe mas não está na landing.
Adicionar após a headline, antes do counter card.

```html
<div class="chart-container">
  <img src="https://raw.githubusercontent.com/rudekwydra/burnless/main/docs/cost_chart.png"
       alt="Standalone O(N²) vs Burnless O(N) cost curve"
       style="width:100%; max-width:720px; border-radius:12px;">
  <p class="chart-caption">Calibrated from real Anthropic API runs. Reproduce: <code>python bench/v2.py --simulate</code></p>
</div>
```

### Gap 2 — Conceito de cápsulas para leigos (MÉDIA prioridade)
Adicionar acima da tabela de números, abaixo da headline:

> "We compress pages of conversation history into a single 80-character line. Your AI keeps the memory. You stop paying for the excess."

### Gap 3 — Case de uso real com números (ALTA prioridade)
Adicionar na seção #pain ou abaixo da tabela existente:

```
Customer support agent — 50 messages exchanged
Without Burnless: $2.45
With Burnless:    $0.28
Real saving:      88%
```

### Gap 4 — Zero-data-retention como USP (MÉDIA prioridade)
Adicionar badge/parágrafo na seção de features:

> "Zero data retention. State lives in `.burnless/` on your machine. No dashboard sees your prompts, your keys, or your conversations."

---

## Frente 3 — Versão PyPI

Bump para v0.4.0 após friendly mode implementado e testado.

### Checklist pré-release
- [ ] `src/burnless/__init__.py` → `__version__ = "0.4.0"`
- [ ] `pyproject.toml` → `version = "0.4.0"`
- [ ] `python -m py_compile src/burnless/**/*.py`
- [ ] `burnless brain -m "oi tudo bem?"` — smoke test friendly mode
- [ ] `python bench/v2.py --simulate` — confirmar números não mudaram
- [ ] `git commit` sem co-autoria
- [ ] `pip publish` via twine

---

## Sequência de execução recomendada

```
Dia 1: Friendly mode (decoder.py + brain.py + config.py) — diamond/codex
Dia 1: Landing gaps 1+3 (chart + case de uso) — silver/sonnet
Dia 2: Landing gaps 2+4 (copy + USP) — silver/sonnet
Dia 2: Testes e smoke test — diamond/codex
Dia 2: Bump versão + commit + PyPI publish
```

---

## Arquivos principais

| Arquivo | Frente | Tier sugerido |
|---|---|---|
| `src/burnless/codec/decoder.py` | Friendly mode | diamond |
| `src/burnless/maestro/brain.py` | Friendly mode | diamond |
| `src/burnless/config.py` | Friendly mode | diamond |
| `site/index.html` | Landing | silver |
| `src/burnless/__init__.py` | Versão | bronze |
| `pyproject.toml` | Versão | bronze |
