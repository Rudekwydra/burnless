# Arquitetura da memória rolante — design pós-fix (d807)

**Data:** 2026-07-05 · **Autor:** Fable (d807, design puro — nenhum código de produção tocado) · **Status:** proposta
**Escopo:** responder Q1–Q7, criticar a opinião preliminar do gold, propor arquitetura + plano faseado.
**Fontes lidas:** `docs/plans/2026-07-04-living-md-hallucination-fix.md`; `src/burnless/recovery.py` (estado pós-fix F1/F2/F3); `src/burnless/epochs_v2.py`; `~/antigravity/forgetless/forgetless.py`; `templates/scripts/burnless_epoch_stop.sh`; `src/burnless/cli.py` (subcomandos epoch).

---

## Resumo executivo

A compactação hoje já é 1 chamada bronze-local por troca (encoder = gemma E4B via ollama) — a premissa "100% paga LLM forte" está **errada**; o problema real é o oposto: chamada demais (toda troca, full-rewrite do doc) num modelo fraco, com cold-start ~20s, e **sem enforcement determinístico de budget** no caminho de compactação (`enforce_budget_v3` só roda no caminho de captura V2/V3, nunca em `compact_pending`). A arquitetura recomendada NÃO é 3 camadas de LLM: é **1 gate determinístico de disco (L0, zero LLM) na frente do rewriter bronze atual (L1), com escalação rara e condicional para modelo forte (L2) só quando gatilhos objetivos disparam**. Camadas decidem QUANDO e COM QUAL modelo compactar — nunca criam caminho de escrita novo: `_validate_candidate` + `write_checkpoint` continuam sendo o único funil. "Refs" vira gramática de linha estruturada (`path — why [seq N]`) parseável por regex, `seq_origem` vira contrato de schema validado fail-closed para entradas novas, e "epoch vira cápsula" é viável hoje sem tocar no Forgetless (uma cápsula por linhagem de sessão, selada no rollover, body auto-contido porque o journal PODA após 256 records). Plano em 6 fases; as 4 primeiras são 100% determinísticas (ganho de tokens sem nenhuma superfície nova de bug de LLM); L2 é a última e só entra se a telemetria justificar.

---

## 1. Respostas Q1–Q7

### Q1 — O que a compactação faz HOJE (mecanismo real) e onde diverge do ideal

**Mecanismo real, ponta a ponta:**

1. **Toda troca** (Stop hook, `templates/scripts/burnless_epoch_stop.sh:66-85`): `extract-exchange` → `journal-append` **síncrono** (record imutável `NNNNNN-<exchange_id>.json`, seq monotônico sob flock — `journal_append`, recovery.py:612) → `compact-pending` **detached em background** (linha 84, `{...} &`).
2. `compact_pending` (recovery.py:800): adquire lease (TTL = encoder.timeout_s+30, recovery.py:203), lê checkpoint, calcula `pending = seqs > applied_through` (recovery.py:844), monta prompt via `_build_compact_prompt` (recovery.py:743) — que desde o fix de ontem delega para `living_rewrite_prompt_v3` + `_SOURCE_TRUST_BLOCK` (recovery.py:736-768).
3. O `rewriter` é `epochs_v2.living_rewriter` (cli.py:1425): com o config atual (`encoder.provider: ollama-local`), é **um POST em `/api/generate` do ollama com gemma-4 E4B local + `ENCODER_SYSTEM_PROMPT`** (epochs_v2.py:646-650). Uma chamada, full-rewrite: o modelo recebe o doc inteiro + trocas pendentes e devolve o doc inteiro reescrito.
4. `_validate_candidate` (recovery.py:774): fail-closed — estrutural (≥1 seção V3 não-vazia), anti-chat (`PERGUNTA:`/`RESPOSTA:`/termina em `?`), anti-seq-fantasma (todo `seq N` do candidate ∈ pendentes ∪ prev_md). Reject → journal fica pendente, `applied_through` não avança.
5. Commit: `harvest_state(candidate)` (best-effort) → `write_checkpoint` com `applied_through = max(seq pendente)` (recovery.py:1012-1020). `write_checkpoint` também **poda o journal** para os últimos 256 records (recovery.py:732 → `_prune_journal`, recovery.py:187; `JOURNAL_RETENTION_RECORDS = 256`, recovery.py:36).
6. Restore: `render_restore` (recovery.py:1245) injeta `living_md` + pendentes verbatim, truncado a `budget_tokens*4` chars (default 2000 tokens) por **corte cego no meio** (`_truncate_text`, recovery.py:1235).

**Divergências do que "deveria" fazer:**

| # | Divergência | Evidência |
|---|---|---|
| D1 | LLM chamado em TODA troca, mesmo "ok, pode ir" — `is_noop`/`_is_trivial_text` (epochs_v2.py:92/43) existem, são determinísticos, e **não são usados no compact path** (só em `apply_capture`, epochs_v2.py:538) | recovery.py:800-874 não tem nenhum gate |
| D2 | Budget de 2500 tokens é só **instrução de prompt** no compact path; `enforce_budget_v3` (epochs_v2.py:463) nunca roda em `compact_pending` — o doc pode crescer se o gemma desobedecer | recovery.py:978-1020: validate → harvest → write, sem enforce |
| D3 | Full-rewrite sempre: mesmo pra anexar 1 fato, o modelo reescreve as 8 seções inteiras → cada compactação re-expõe TODO o doc ao risco de paráfrase/mutação (a regra VERBATIM do prompt v3 mitiga, não elimina) | epochs_v2.py:404-459 |
| D4 | `preserve_guard` (anti-perda de Contracts, epochs_v2.py:196) também só roda no caminho de captura, não no compact path | recovery.py:1012 escreve o candidate direto |
| D5 | Conteúdo evaporado morre de verdade: 'Recuperáveis' guarda pointers dNNN se o modelo obedecer, mas não há memória fria estruturada; o journal poda em 256 e o overwrite de geração destrói o resto | recovery.py:36, 732 |
| D6 | Truncation do restore é corte cego no meio do doc (pode cortar seção ao meio) | recovery.py:1235, 1334 |

O desenho de intenção (doc vivo pequeno + journal como fonte de verdade + degradação checkpoint+delta) está **correto e funcionando**; as divergências são de disciplina de execução (gates ausentes, enforcement ausente, ausência de camada fria).

### Q2 — Crescimento do living_md: sub-linear? throttle real?

**Deveria:** sim — quase-constante (O(budget), não O(trocas)). O modelo mental correto é "doc mutante de tamanho fixo": conteúdo entra, conteúdo evapora para memória fria, o tamanho oscila em torno do budget.

**Hoje:** o crescimento é *bounded-if-obedient*. As forças que limitam tamanho são: (a) a instrução `Mantenha todo o doc sob ~2500 tokens` no prompt (epochs_v2.py:419), interpretada por um E4B quantizado — obediência não garantida; (b) o corte cego de `render_restore` a 2000 tokens na hora de injetar (recovery.py:1314), que limita o que o assistente VÊ mas **não** o que o checkpoint ARMAZENA (checkpoint pode inchar e o restore passa a truncar-sempre, perdendo seções inteiras silenciosamente). O throttle determinístico que existe no código (`enforce_budget_v3`, com ordem de poda Decisões→Refs→Riscos e invariantes de Contracts pinados) **não é chamado no compact path** — só em `apply_capture` (epochs_v2.py:576). Ou seja: `budget_tokens=2500` é real como *pedido*, não como *garantia*, exatamente no caminho que virou o principal (PTY/hooks usa `compact_pending`, não `apply_capture`).

**Resposta:** sim ao crescimento quase-constante como contrato; e o mecanismo pra isso já existe pronto — falta 1 chamada de função no lugar certo (F2 abaixo). Nenhuma arquitetura nova é necessária pra resolver Q2.

### Q3 — "Refs" estruturado: vale a pena?

**Sim, com uma ressalva de forma.** Hoje `harvest_state` (epochs_v2.py:149) já extrai `refs` como lista de strings livres para `harvested_state` no checkpoint — a metade estruturada do trabalho existe; falta a gramática. Valor concreto:

- Busca determinística sem LLM: "quais arquivos fundamentaram as decisões desta sessão?" vira regex sobre `state.json`, não releitura do living_md.
- É o insumo direto do L0 (Q4): comparar `files` do journal contra refs conhecidas exige refs parseáveis.
- É o insumo direto da cápsula (Q6): refs estruturadas viram body de cápsula com seções limpas.

**Ressalva:** NÃO transformar o living_md em JSON. O doc é lido por humano e por assistente em restore; markdown legível é feature. A forma certa é **gramática de linha** dentro do markdown + parse determinístico para objetos no `harvested_state`:

```
## Refs
- /Users/roberto/antigravity/burnless/src/burnless/recovery.py#L800-1049 — compact_pending: fluxo de lease+validate [seq 12]
- /Users/roberto/antigravity/burnless/.burnless/config.yaml — encoder ollama-local [seq 3-5]
```

Gramática (regex ilustrativa):
`^- (?P<path>/\S+?)(?:#L(?P<l1>\d+)(?:-(?P<l2>\d+))?)? — (?P<why>.+?) \[seq (?P<seq>\d+(?:-\d+)?)\]$`

`harvested_state.refs` passa de `list[str]` para `list[{path, lines?, why, seq}]` (com fallback tolerante: linha que não casa a gramática permanece como string legacy — migração suave, nada quebra). "Recuperáveis" ganha a mesma gramática com `dNNN` no lugar de path. Custo: baixo (parser + 3 linhas no prompt v3 exigindo o formato + 1 check no validador). Ganho: alto. **Vale.**

### Q4 — Camada 0 (disco puro, sem LLM) pra calcular delta antes de gastar LLM

**Sim, e ~80% dela já está escrita.** Peças existentes, todas determinísticas e sem LLM:

- **Arquivos tocados:** cada record do journal já carrega `files` extraído dos tool_use do transcript (`_extract_files_from_content`, recovery.py:305; populado em `extract_exchange`, recovery.py:373). Diff = `set(union(r.files for r in pending)) - set(refs conhecidas do checkpoint)`. Zero parsing novo.
- **Entidades novas:** `extract_entities` (epochs_v2.py:69 — paths, dNNN, hashes, file.ext) sobre `pending` vs sobre `prev_md`. Já usado pelo `is_noop`.
- **Trivialidade:** `is_noop` + `_is_trivial_text` (epochs_v2.py:92/43 — "ok", "pode ir", perguntas curtas). Já testado no caminho de captura.
- **Volume:** `len(user_text+assistant_text)//4` por record (a mesma heurística de tokens usada em todo o codebase).

O L0 é literalmente uma função pura `should_compact(checkpoint, pending, source) -> {skip | l1 | l2}` que compõe essas quatro peças. Custa <10ms de disco (os records pendentes são JSONs pequenos já no filesystem local). O ponto arquitetural crítico: **o L0 nunca avança `applied_through` e nunca escreve nada** — ele só decide se a chamada LLM acontece agora ou fica pra depois. Adiar é seguro por construção: o journal é durável e `render_restore` já degrada para checkpoint+delta (pendentes injetados verbatim, recovery.py:1294-1310). Skip do L0 = economia pura, sem risco de perda.

### Q5 — Pipeline em camadas disco→bronze→forte? Critérios de promoção?

**Sim à estrutura em camadas, não ao desenho proposto.** Correção de premissa primeiro: **o rewriter "caro" de hoje JÁ é o bronze** (encoder = gemma E4B local, epochs_v2.py:646; não existe nenhuma chamada de modelo forte no pipeline de memória atual). Então "disco → bronze → rewriter atual" descreveria disco → bronze → bronze. O desenho correto:

- **L0 — gate determinístico (disco, grátis):** decide skip/compacta/escala. É o Q4.
- **L1 — rewriter bronze atual (barato, ~sempre):** exatamente o `compact_pending` de hoje, mas invocado em **batch com debounce** (várias trocas pendentes por chamada) em vez de por troca. Isso também dissolve o problema do cold-start ~20s: 1 chamada a cada K trocas amortiza o load do modelo, e a chamada já é detached (o PTY interativo nunca espera — burnless_epoch_stop.sh:84).
- **L2 — modelo forte (raro, condicional):** mesma interface `rewriter(prompt)`, mesmo prompt v3, mesmo validador, mesmo writer — só o modelo muda (branch `claude -p` já existe em living_rewriter, epochs_v2.py:673). Entra apenas quando gatilhos estruturais disparam.

**Critérios de promoção — todos contáveis, todos testáveis com fixtures determinísticas:**

| Transição | Gatilho (OR entre eles) | Por que é objetivo |
|---|---|---|
| L0 → skip | `is_noop(prev_md, troca)` ∧ `files ⊆ refs` ∧ `entities(pending) ⊆ entities(prev_md)` ∧ `source == stop` | 4 predicados booleanos sobre dados em disco |
| L0 → L1 | `len(pending_não_triviais) ≥ K` (default 3) ∨ `Σ tokens(pending) ≥ X` (default 1200) ∨ `source ∈ {clear, end}` (rollover SEMPRE compacta) ∨ `idade(pendente mais antigo) ≥ T` (default 15min) | contagens e timestamps; nenhum julgamento semântico |
| L1 → L2 | `tokens(living_md) > budget` APÓS `enforce_budget_v3` ter podado (doc não cabe sem reestruturar) ∨ `compaction_rejected` do L1 ≥ 2 consecutivos (telemetria já existe: recovery.py:984) ∨ `generation % 25 == 0` (consolidação periódica) | overflow numérico, contador de eventos do owner_loop.jsonl, módulo |

Nota anti-heurística-frágil: nenhum gatilho acima é "regex tentando entender contradição" (isso seria a mesma classe de fragilidade do bug de ontem). Contradição semântica continua sendo tarefa do MODELO (a regra Supersede do prompt v3, epochs_v2.py:429) — os gatilhos só medem **quantidade, idade, overflow e taxa de rejeição**, que são sinais mecânicos.

### Q6 — Epoch vira cápsula Forgetless: viável?

**Viável hoje, sem tocar uma linha do Forgetless.** Verificado no schema real (forgetless.py):

- Cápsula = JSON plano em `~/.forgetless/capsules/<name>.json` com `{name, tags, linked, summary, body, emotional_weight, created, updated}` (`cmd_new`, forgetless.py:287-309). Criação via CLI: `forgetless new NAME --summary "..." --tag X --link Y --body-file /tmp/f.md`; atualização via `forgetless update` (forgetless.py:428).
- Ranking: BM25 sobre name+tags+summary+body, PageRank sobre o grafo `linked` (peso 10.0 — dominante, forgetless.py:618), recency half-life 30d, RRF semântico. Implicações diretas de design: (a) **`linked` importa muito** — encadear cada cápsula de epoch à anterior cria autoridade de PageRank na linhagem; (b) summary/tags carregam o BM25 — precisam de keywords reais, não boilerplate.
- Gotcha 1: `cmd_new` falha se o nome existe (forgetless.py:289) → selar de novo a mesma sessão exige `update`, ou nome novo.
- Gotcha 2: `_maybe_auto_pin` (forgetless.py:278) auto-pina cápsulas com tags `handoff|risk|blocker|read-first` → **não** usar essas tags no seal automático, senão toda sessão vira pin.
- Gotcha 3 (o mais importante): **o journal não é permanente** — poda em 256 records (recovery.py:36). Logo "link pro range de seq no journal" é dica de proveniência, não referência resolvível para sempre. O body da cápsula precisa ser **auto-contido** (conteúdo selado copiado, não apontado).

**Mecânica recomendada (detalhe completo na §3):** 1 cápsula por **linhagem de sessão** (não por generation — fee436ab teve 16 generations numa sessão; 1 cápsula/generation seria poluição), selada no rollover/session-end e atualizada via `update` se a linhagem continuar. Fail-open obrigatório: subprocess `forgetless new/update` com timeout curto; se o binário não existir ou falhar, a memória quente segue intacta (mesmo padrão dos `_log_access`/`_emit_signal` do próprio Forgetless). Dependência externa: **nenhuma mudança no Forgetless é necessária**. (Opcional, não-bloqueante: uma tag reservada tipo `burnless-epoch` pra facilitar `forgetless list --tag`; isso é convenção, não código.)

### Q7 — Critério de linearidade mandatório no schema

**Contrato mínimo proposto:** *toda entrada NOVA em Decisões, Riscos e Refs carrega `[seq N]` ou `[seq N-M]` com N,M ∈ seqs reais do journal; entradas herdadas do prev_md carregam o marcador que já tinham (ou nenhum, se legacy).*

O "nova vs herdada" é decidível deterministicamente graças à regra VERBATIM do prompt v3 (epochs_v2.py:436): entrada mantida tem núcleo que é substring exata do prev_md. Então o validador consegue: `entrada não-substring de prev_md` ∧ `sem [seq N]` → reject `missing_seq_origem`; `[seq N]` com N ∉ pendentes ∪ prev_md → já rejeitado hoje (anti-fantasma, recovery.py:791). Isso completa o par: ontem fechamos "não pode citar seq que não existe"; o contrato de Q7 fecha "não pode existir fato novo sem citar seq".

**Por que mandatório-com-escape e não mandatório-absoluto:** exigir seq em TODAS as entradas (incluindo herdadas legacy sem marcador) tornaria todo checkpoint pré-contrato invalidável e forçaria o modelo fraco a *inventar* seqs pra linhas antigas — induzindo exatamente a alucinação que o resto do sistema combate. O escape para herdadas preserva compatibilidade e mantém o contrato 100% verificável. `Foco atual`/`Threads abertas` ficam de fora do mandatório na v1 (mudam de redação a cada compactação por natureza; forçar seq nelas geraria taxa de rejeição alta no E4B com pouco ganho — reavaliar com telemetria de `compaction_rejected`).

---

## 2. Crítica da opinião preliminar (gold)

**#1 — "Camadas disco→bronze→rewriter forte; hoje 100% paga o preço de pensar (LLM forte)": CONCORDO com a estrutura, DISCORDO da premissa e de metade do desenho.** A premissa é factualmente errada: o rewriter de `compact_pending` é o encoder bronze-local (gemma E4B via ollama — epochs_v2.py:646 + config real citado no doc de ontem), não um modelo forte. Hoje 100% das compactações pagam o preço de *uma chamada bronze por troca* — o desperdício real é frequência (toda troca, full-rewrite, cold-start ~20s), não tier. O desenho corrigido é: gate determinístico (L0) + **debounce/batch** na frente do bronze atual (L1), com o modelo forte entrando como L2 raro e condicional — quase o inverso da ênfase original. O maior ganho de tokens/latência da proposta inteira está no batch do L1, não na existência do L2.

**#2 — "Refs estruturado": CONCORDO, com correção de forma.** Estruturar sim, mas como gramática de linha dentro do markdown (parseável por regex, humano-legível) + objetos no `harvested_state` — não como formato de dado separado do doc. Ver Q3. Metade da infra já existe (`harvest_state` já popula `harvested_state.refs`).

**#3 — "Linearidade como contrato de schema": CONCORDO integralmente.** É o fechamento natural do F3 de ontem — a validação anti-fantasma vira contrato bidirecional (Q7). Custo marginal: ~1 check novo em `_validate_candidate`.

**#4 — "Epoch → cápsula quando thread fecha": CONCORDO com a direção, DISCORDO da granularidade e de um detalhe técnico.** (a) Granularidade: selar por thread-fechada exige detectar "thread fechou" — que é julgamento semântico do modelo, ou seja, mais uma superfície do tipo que causou o bug de ontem. Selar por **rollover/session-end** é gatilho mecânico (o hook de end já existe e já roda `compact-pending` — burnless_epoch_end.sh:94), captura o doc no seu estado mais consolidado, e 1 cápsula/linhagem não polui o Forgetless. Selar por thread pode vir depois como refinamento, quando houver marcador estrutural confiável. (b) Detalhe técnico: "com link pro range de seq no journal" não sobrevive à poda de 256 records (recovery.py:36) — o body precisa copiar o conteúdo, o range de seq é metadado de proveniência, não referência viva.

**#5 — "Separar investigação de pensamento (Maestro/Worker recursivo)": CONCORDO no princípio, com um alerta de overengineering.** A "investigação" da memória rolante é quase toda determinística (regex/set-diff sobre JSONs pequenos locais — Q4). Colocar um bronze pra "achar o que precisa ser lido" onde um `set.issubset` resolve é pagar 20s de cold-start pra evitar 10ms de disco. O padrão Maestro/Worker aplica-se ao **L2**: quando o modelo forte for acionado, ele deve receber o delta já extraído pelo L0/L1 (investigação pronta) e só decidir estrutura/contradição (pensamento). Não aplicar o padrão onde não há investigação não-trivial a fazer.

**Sobre os riscos que o gold levantou — avaliação:**

- *"3 camadas = 3 superfícies do mesmo bug de ontem"* — risco real e é O argumento decisivo contra camadas-como-transformações. Neutralizado pelo princípio central da §3: **camadas são gates de decisão, não escritores**. Só existe UM caminho de escrita (rewriter → `_validate_candidate` → `write_checkpoint`), qualquer que seja a camada que o acionou. L0 não tem LLM (não pode alucinar); L2 usa o mesmo prompt e o mesmo validador do L1. A superfície de bug de LLM continua sendo exatamente 1.
- *"Cold-start ~20s do bronze em toda troca"* — concordo, e a resposta é o debounce do L0: menos chamadas e amortizadas. Além disso a chamada já é detached (não bloqueia o PTY); o custo era térmico/VRAM, não latência percebida.
- *"Critério de promoção testável, não heurística frágil"* — concordo; todos os gatilhos da tabela em Q5 são contagens/timestamps/overflow, testáveis com fixtures sintéticas sem nenhum LLM. A linha vermelha explícita: **nenhum gatilho tenta interpretar semântica por regex**.
- *"Complexidade do recovery.py"* — parcialmente concordo. F1–F4 adicionam ~2 funções puras e ~4 chamadas; o grosso reusa código existente (`is_noop`, `enforce_budget_v3`, `extract_entities`). O seal (F5) deve ir em **módulo novo** (`sealing.py` ou similar), não dentro do recovery.py — é fluxo fail-open independente. O L2 (F6) é a única complexidade genuinamente nova, e por isso é a última fase e condicional a dados.

---

## 3. Arquitetura recomendada

### 3.1 Fluxo de dados

```
                     TODA TROCA (Stop hook — já existe)
                     ────────────────────────────────
transcript ──► extract-exchange ──► journal-append (sync, determinístico, seq++)
                                        │
                                        ▼ (bg detached, como hoje)
                              ┌─────────────────────┐
                              │ L0 · GATE (disco)    │  0 LLM, <10ms
                              │ should_compact(...)  │
                              └─────────┬───────────┘
              skip ◄──────────┬─────────┤
      (pendentes ficam;       │         │ gatilhos L1 satisfeitos
       journal é durável;     │         ▼
       restore degrada p/     │  ┌──────────────────────────┐
       checkpoint+delta)      │  │ L1 · COMPACT (bronze)     │  gemma E4B local
                              │  │ prompt v3 + trust block   │  BATCH das pendentes
                              │  │ (= compact_pending atual) │  (1 call por K trocas)
                              │  └──────────┬───────────────┘
     gatilhos L2 (raros) ─────┘             │ candidate
                              ▼             ▼
                   ┌────────────────┐  ┌────────────────────────────┐
                   │ L2 · RESTRUCT  │  │ FUNIL ÚNICO DE ESCRITA      │
                   │ (modelo forte, │─►│ _validate_candidate (fail-  │
                   │ mesmo prompt,  │  │ closed) → enforce_budget_v3 │
                   │ branch claude  │  │ (determinístico) → harvest  │
                   │ -p já existe)  │  │ → write_checkpoint          │
                   └────────────────┘  └──────────┬─────────────────┘
                                                  │
                     ROLLOVER / SESSION-END       ▼
                     ─────────────────────  checkpoint.json + living.md + state.json
                     handoff-write ──► SEAL (fail-open, subprocess)
                                        └─► forgetless new/update
                                            epoch-<project>-<sid8>
                                            (memória fria, BM25+PageRank)
```

Invariantes de projeto (o que impede a regressão da classe de ontem):

1. **Um único caminho de escrita.** L0/L2 são decisão de roteamento; validador e writer são compartilhados. Bug de prompt/modelo em qualquer camada morre no mesmo `_validate_candidate`.
2. **L0 nunca escreve, nunca avança `applied_through`.** Skip = adiamento, não consolidação. Perda impossível por construção (journal durável + degradação checkpoint+delta já existente).
3. **Fail-closed na quente, fail-open na fria.** Compactação rejeitada → pendentes ficam (como hoje). Seal falhou → loga e segue (cápsula é bônus, nunca bloqueia).
4. **Nenhum gatilho semântico por regex.** Gatilhos = contagem, tokens, idade, overflow, taxa de rejeição. Semântica é trabalho de modelo sob validador.

### 3.2 Critérios de acionamento (consolidado, com defaults e config)

Config proposto (`epochs.compact` em config.yaml — todos com default, zero breaking change):

```yaml
epochs:
  compact:
    min_pending_nontrivial: 3    # L0→L1: trocas não-triviais acumuladas
    min_pending_tokens: 1200     # L0→L1: OU volume acumulado (len//4)
    max_pending_age_s: 900       # L0→L1: OU pendente mais antigo passou de 15min
    # rollover (source clear/end) SEMPRE compacta — não configurável
    l2_reject_streak: 2          # L1→L2: rejeições consecutivas do bronze
    l2_generation_every: 25      # L1→L2: consolidação periódica
    # L1→L2 também dispara se doc > budget após enforce_budget_v3
```

Testabilidade: cada gatilho tem teste puro com fixtures (journal sintético + checkpoint sintético), sem LLM e sem ollama — mesma classe dos testes F6 do plano de ontem.

### 3.3 Schema de Refs estruturado

Gramática de linha (no living_md, seções `Refs` e `Recuperáveis`):

```
- <path_absoluto>[#L<início>[-<fim>]] — <why, prosa curta> [seq <N>[-<M>]]
- d<NNN> — <dica de comando> [seq <N>]          (variante Recuperáveis)
```

Objeto parseado (em `harvested_state`):

```json
{"path": "/Users/roberto/antigravity/burnless/src/burnless/recovery.py",
 "lines": [800, 1049],
 "why": "compact_pending: fluxo de lease+validate",
 "seq": [12, 12]}
```

Regras: parser tolerante (linha fora da gramática → mantida como string legacy, contada em telemetria `refs_unparsed`); prompt v3 ganha o formato como instrução com exemplo; `_validate_candidate` ganha check brando na v1 (telemetria) que endurece pra reject quando a taxa de conformidade do E4B for conhecida.

### 3.4 Epoch → cápsula: mecânica

- **Quando sela:** no rollover/session-end (hook end já dispara compactação final — burnless_epoch_end.sh:94; o seal entra logo após, no mesmo fluxo detached). Nunca por generation; thread-close fica pra depois, se um dia houver marcador estrutural confiável.
- **O que sela:** o `living_md` consolidado (auto-contido — copiado, não apontado) + bloco de metadados: `host_session_id`, `generation`, range de seq consolidado (`1..applied_through`, como proveniência), path do journal_dir, timestamp.
- **Nome:** `epoch-<project>-<sid8>` (sid8 = 8 primeiros chars do host_session_id; project = nome do diretório do root). 1 cápsula por linhagem: se já existe (rollover múltiplo da mesma linhagem via inherit), `forgetless update --append` em vez de `new`.
- **Tags:** `burnless-epoch`, `<project>` — e **nunca** `handoff`/`risk`/`blocker`/`read-first` (evita o auto-pin de forgetless.py:278).
- **Summary:** primeira linha de `## Foco atual` (é o campo que o BM25 e o embedder leem — conteúdo real, não boilerplate).
- **Linked:** cápsula de epoch anterior do mesmo projeto (cadeia de PageRank na linhagem) + dNNNs citados em Recuperáveis que tenham cápsula.
- **Transporte:** subprocess `forgetless new/update` com timeout ~10s, fail-open total. Se o CLI não estiver no PATH, evento `seal_skipped` no owner_loop.jsonl e vida que segue.
- **Dependência externa:** nenhuma mudança no Forgetless. (Conveniência futura opcional: nada bloqueia.)

Efeito líquido: o living_md quente para de ser o único sobrevivente — o que evapora do doc quente continua pesquisável via `forgetless rank`, e a linearidade (range de seq + linhagem linkada) fica registrada na memória fria.

---

## 4. Plano faseado

Ordem deliberada: F1–F4 são 100% determinísticas (ganho de tokens/robustez sem nenhuma superfície nova de LLM); F5 é fail-open isolado; F6 é condicional a telemetria. Cada fase é um commit independente com testes próprios, nada quebra o fluxo atual se a fase seguinte nunca acontecer.

**F1 · L0 gate + debounce (silver).** Nova função pura `should_compact(checkpoint, pending, source, cfg) -> Literal["skip","compact"]` em recovery.py (ou módulo novo `gating.py`), composta de `is_noop`/`extract_entities`/`files ⊆ refs`/contadores; chamada no topo de `compact_pending` antes do prompt; config `epochs.compact.*` com defaults. Skip loga `compaction_deferred` com razão. Testes: fixtures sintéticas por gatilho (noop puro, K não-triviais, volume, idade, rollover força). *Maior ganho de custo da proposta inteira; zero LLM.*

**F2 · Budget determinístico no compact path (bronze).** Em `compact_pending`, após `_validate_candidate` OK: `candidate = enforce_budget_v3(candidate, budget_tokens=...)` (+ `preserve_guard` — avaliar na spec; fecha D2 e D4). Testes: candidate sintético acima do budget → doc final ≤ budget, ordem de poda Decisões→Refs→Riscos respeitada, Contracts pinados sobrevivem.

**F3 · Refs/Recuperáveis estruturados (silver).** Gramática da §3.3: parser tolerante em epochs_v2 (upgrade de `harvest_state`), instrução+exemplo no prompt v3, telemetria `refs_unparsed`. Testes: parse de linhas válidas/legacy/malformadas; round-trip harvest.

**F4 · Contrato seq_origem (silver).** Check em `_validate_candidate`: entrada nova (não-substring do prev_md, modulo decorações de faixa/provenance) em Decisões/Riscos/Refs sem `[seq N]` válido → reject `missing_seq_origem`. Começa atrás de flag de config (`epochs.compact.require_seq_origem: false` default) até a taxa de conformidade do E4B ser medida via telemetria; vira default true depois. Testes: candidates com/sem marcador, herdadas legacy passam.

**F5 · Seal epoch→cápsula (silver, módulo novo).** `sealing.py` com `seal_epoch(root, host, sid) -> dict` (fail-open, subprocess forgetless, mecânica da §3.4); chamado no fluxo do hook end após a compactação final. Testes: `FORGETLESS_ROOT` temporário → cápsula criada com name/tags/linked corretos; forgetless ausente → `seal_skipped`, exit limpo; segunda selagem da mesma linhagem → update, não erro.

**F6 · L2 escalação pra modelo forte (gold — CONDICIONAL).** Só implementar se a telemetria pós-F1..F4 mostrar necessidade (taxa de `compaction_rejected` do bronze alta, ou docs batendo em overflow recorrente). Roteamento por gatilhos da §3.2; reusa `living_rewriter` branch anthropic (epochs_v2.py:673) com o mesmo prompt/validador/writer. Testes: gatilhos de escalação por fixture; L2 rejeitado também degrada pra checkpoint+delta. *Se os dados não justificarem, esta fase morre — e a arquitetura continua completa sem ela.*

Pré-requisito operacional (fora das fases, já planejado ontem): F4 do doc de 2026-07-04 (remediação dos checkpoints corrompidos) deve aterrissar antes do F5 daqui, senão o primeiro seal congela ficção em cápsula.

---

## 5. O que este design NÃO propõe (anti-overengineering explícito)

- **Não** propõe bronze pra "investigar o que ler" — set-diff em disco resolve (Q4/crítica #5).
- **Não** propõe 3 chamadas LLM em cadeia por compactação — o caso comum continua sendo 0 (skip) ou 1 (bronze batch).
- **Não** propõe detectar contradição/thread-close por regex — semântica fica no modelo, sob validador fail-closed.
- **Não** propõe living_md em JSON — markdown legível com gramática de linha parseável.
- **Não** propõe mudanças no Forgetless — seal usa o CLI existente, fail-open.
- **Não** propõe auditor-LLM (LLM-julga-LLM) em nenhuma camada — validação é 100% determinística, coerente com a aposentadoria do auditor v9.
