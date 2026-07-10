# COUNTDOWN DISCIPLINE — Burnless rumo ao HN · 06/07 → 27/07/2026

**Fonte de verdade tickável.** Qualquer LLM (Opus/Sonnet/Fable) que trabalhar com Roberto DEVE: (1) ler este arquivo no início da sessão se o assunto for burnless; (2) ticar `[x]` no que foi comprovadamente feito, com data e evidência de 1 linha; (3) apontar o próximo item não-ticado; (4) NUNCA ticar sem evidência. Capsula forgetless espelho: `countdown-discipline-burnless` (pinada até 28/07).

**Regra de ouro:** nenhum item de semana N+1 antes dos gates da semana N. Se um dia falhar, anotar no Diário e seguir — disciplina é cadência, não perfeição.

---

## SEMANA 1 · 06–12/07 — Provar em casa (dogfood + calibração)

### Setup (hoje, 06/07)
- [x] `burnless doctor` verde (drift zero, hooks wired, hook_errors vazio) — 06/07, 21 PASS / 1 WARN (D3 mcp list timeout, benigno) / 0 FAIL
- [x] `forgetless harvest --apply` com ollama vivo — primeira capsule real nascida de um export v1 (evidência: nome da capsule) — 06/07, 3 capsules: sweep-sort-filter-inverted (burnless), ank-v10-pipeline-cobertura + proposta-html-negrito-estrategico (leads); 69 sessões ledgered
- [x] Delegar Fase E aos workers: `docs/plans/DELEGACAO_FASE_E.md` (E1 bench, E2 CI, E3 release) — 06/07, E1 já entregue
- [ ] Decidir plist do sweep (aprovar ou adiar explicitamente)

### Dogfood diário (07–12/07) — quantificado
- [ ] 07/07 — 1 sessão densa (≥90min ou ≥20 turnos) com `burnless-planner` como agente, ≥2 `/clear` no meio, conferir restore (Foco atual + última troca literal + `## Manifesto`). Perdeu fio? ___
- [ ] 08/07 — idem. Perdeu fio? ___
- [ ] 09/07 — idem. Perdeu fio? ___
- [ ] 10/07 — idem + **F5: rodar calibração de pesos** (worker gated; revisar antes de carregar no config)
- [ ] 11/07 — idem. Perdeu fio? ___
- [ ] 12/07 — idem + revisar `.burnless/logs/hook_errors.log` da semana (deve estar limpo)

**Meta da semana:** ≥6 sessões densas, ≥12 `/clear`, zero perda de fio não-explicada. Toda perda = evidência guardada (copiar o restore ruim para `docs/audits/dogfood/`).

### GATE SEMANA 1 (12/07) — só avança se:
- [ ] Zero perda de fio OU todas as perdas diagnosticadas e corrigidas por worker
- [x] E1 (bench reprodutível) entregue pelos workers — números de p50/p95 e economia com data — 06/07, p50 138ms/p95 144-151ms; economia CRESCE com turnos: 20t=14.7% / 50t=73.9% / 100t=86.9% (sweep, commit 8303e37), curva satura ~87%; auditado (roda, reproduz ±10%, exit 1 loud sem deps). Pendência higiene: dropar key legacy token_economy (54.4%, cadência antiga) do JSON
- [ ] E2 (CI runner limpo) verde
- [ ] Harvest nightly rodou ≥3x sem sujeira (aprovar plist H2 se ainda manual)

## SEMANA 2 · 13–19/07 — Provar fora de casa (testers externos)

- [ ] 13–15/07 — Recrutar 3 a 5 testers reais (dev amigos, Discord/comunidade Claude Code). Critério: máquina que não é a sua, sem você olhando
- [ ] Cada tester: install → `burnless setup` → prova viva → 1 sessão real com `/clear`. Coletar: onde travou nos primeiros 5 minutos (formulário simples ou DM)
- [ ] 16–18/07 — Triagem: cada travada vira issue; delegar fixes aos workers (1 commit por issue, mesmo protocolo P6)
- [ ] Dogfood de manutenção: ≥3 sessões densas na semana (seguir formato da semana 1)
- [ ] 19/07 — Re-teste com pelo menos 2 testers após os fixes

### GATE SEMANA 2 (19/07) — só avança se:
- [ ] ≥3 testers completaram a prova viva sozinhos, sem suporte por chat
- [ ] Zero issue crítica aberta (crítica = quebra instalação, memória ou terminal)
- [ ] CI continua verde com os fixes da semana

## SEMANA 3 · 20–27/07 — Lançar

- [ ] 20–21/07 — Regravar demo asciinema (30–45s): sessão densa → `/clear` → fio de volta. Sem cortes mágicos
- [ ] 21/07 — README final: quickstart 5 linhas no topo, claims SÓ do bench E1 (com data/versão), manifesto a 1 link de distância
- [ ] 22/07 — Sessão com Fable: revisão do README + rascunho do post Show HN (claims mensuráveis, demo como prova central, tom honesto sobre limites)
- [ ] 23/07 — Release: checklist E3 completo (smokes verdes, drift zero, public_git_check, CHANGELOG, tag de versão)
- [ ] 24–25/07 — FREEZE: só bugfix. Nada de feature nova (anotar ideias em capsula, não em código)
- [ ] 26/07 — Ensaio do lançamento: reler post, preparar respostas para as 5 perguntas óbvias do HN (energia/números, "por que não RAG", segurança das capsules, lock-in, benchmark)
- [ ] 27/07 — **GO/NO-GO** com Fable contra os 5 critérios de aceitação do P6 + as 3 semanas deste arquivo. Se GO: postar terça ou quarta de manhã (horário US), e ficar 4h disponível respondendo

### Critério de NO-GO honesto
Qualquer perda de fio não-resolvida na semana 3, ou tester travando na instalação, = adia 1 semana. HN é one-shot por ciclo; adiar custa menos que queimar a segunda chance.

---

## Diário (LLMs preenchem, 1 linha por evento)

| Data | Evento | Evidência |
|------|--------|-----------|
| 06/07 | Countdown criado; P6 completo (A1-A5, S1-S3, H0-H1, I1-I3, F1-F7 não-gated) | commits nos 2 repos, suites verdes |
| 06/07 | E1 entregue: restore p50 139ms / p95 151ms, llm_calls=0, economia 54.5% (23 turnos, 3 rollovers), estável run1×run2 (Δp50 0.005ms) | bench/run_public.sh |
| 06/07 | Observação forgetless: `update` via MCP >30s (embed síncrono, ollama frio) — candidato a fix | timeout no MCP |
| 06/07 | E1-B ENTREGUE: sweep confirma a tese — economia sobe 20t=14.7% → 50t=73.9% → 100t=86.9% (satura ~87%), curva "trajetória" pro README. Baseline O(N²) via reenvio de transcript acumulado; burnless O(N) via payload limitado ~2k | commit 8303e37, JSON public_20260707T024720Z |
| 06/07 | Auto-correção (2ª): alarmei "README contraditório 54% vs 15%" — FALSO, markdown mostra só o sweep. Real: só a key legacy token_economy (54.4%, cadência antiga) sobra morta no JSON, a dropar | leitura do by_rollover no JSON |
| 06/07 | Setup do dia fechado: doctor verde + harvest real (3 capsules) + E1 auditado e aprovado | COUNTDOWN linhas 12-14, commit 12fadca |
| 06/07 | Dogfood/fricção: 1º dispatch de E1 abortou no preflight — backtick literal ``` ``` ``` no `## Verify` quebra `/bin/sh` (command-subst). Fix: só greps de 1 linha. Reincidente conhecido | d817 ABORTED → d818 OK |
| 06/07 | Auto-correção de auditoria: `... \| tail; echo $?` reporta exit do `tail`, não do script — quase re-despachei fix de bug fantasma (script já saía exit 1 loud) | re-medido sem pipe |
| 07/07 | DOGFOOD MARCO: /clear real com 216k → retomada em 5k, fio perfeito, re-leituras redundantes = 0 (sessão a7591699). O produto aconteceu com o dono sem ele sentir | transcript + medição do worker |
| 07/07 | E1-D (transcripts reais, 302 JSONL): c≈1.7-2.1k/turno, K seed≈49.6k, R=16.6k-66.2k, fricção≈0 → T*≈7.4-16.8k vs config 120k (~8× acima). Decisão Fable+Roberto: baixar rollover_at_tokens para 40k (1 linha, config.py:199), FIXO até 27/07; descer mais só após A/B remeasure-rtk-thesis-2026-07-26. Flag: seed frio de ~50k (CLAUDE.md+soul) é a alavanca seguinte — dieta do seed baixa K e R juntos | capsula paper-opp-e1d-tstar-real-transcripts |
| 06/07 | Frase para o post (não perder): "I'm the first person in the world who isn't afraid of /clear. You can be the second." — abre o corpo do post; título do Show HN fica sóbrio e descritivo | ideia Roberto+Fable, meia-noite |
| 10/07 | **FIX FUTURO (preflight)**: `php -l FILE` no `## Verify` aborta o preflight como "malformed check (crashes instead of cleanly exiting 1)" — o `php -l` emite stdout ("No syntax errors detected") mesmo com rc=0, e `_preflight_verify_block` trata check com output em rc=0 como malformado. Workaround manual hoje: `php -l FILE > /dev/null 2>&1`. **Candidato a fix**: o preflight deveria julgar SÓ pelo exit code, não pela presença de stdout (ou whitelistar `php -l`). Custou 1 dispatch abortado (d010→d011) no app_aranda | d010 ABORTED → spec com redirect → d011 OK 8/8 |
| 06/07 | E1-C proposto (Fable): pergunta do Roberto sobre cadência. (1) sweep atual correto (k fixo=8, N varia); (2) refinar pontos em múltiplos de k (24/48/96); (3) cadência ótima teórica k*=√(R/c) — estimar c e R do by_rollover, prever k*, varrer k∈{4,6,8,12,16} @ N=96 e conferir se mínimo empírico bate. Produção: sweep real é no rollover_at_tokens, k por turnos é proxy. Delegar amanhã, não bloqueia gate | fórmula de (N/k)·(c·k²+R) |
