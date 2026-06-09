# Resume 2026-06-03 — Dissecar burnless de ponta a ponta + pendências

Escrito 2026-06-02 fim de sessão. Roberto quer revisar isto com calma amanhã.

## 1. PEDIDO PRINCIPAL — dissecar burnless do início ao fim
Roberto quer reinvestigar TODO o burnless, dissecar o conceito, entender tudo que
roda em runtime. Não é debug pontual — é mapa completo. Sugestão de eixos:
- **Camadas:** Encoder/Decoder, Maestro, Workers (PROTOCOL.md = fonte canônica).
- **Caminhos de execução de um `burnless do`:** route → delegate → run →
  (cached_worker SDK | live_runner CLI | maestro) → capsule → verify gate.
  Mapear QUAL caminho dispara em quais condições (flags/config).
- **Onde cada call bate:** API paga (SDK) vs assinatura (CLI claude -p). Listar
  TODA chamada de modelo e sua origem de auth. Esta foi a dor de hoje.
- **Cache:** prefix-cache automático do CLI vs cache_control explícito do SDK
  vs warm_session fork (live_runner:309) vs keepalive daemon.

## 2. INSEGURANÇA DO ROBERTO — warm cache precisa de monitoramento + disparo automático
Hoje o warm cache na assinatura depende de:
- `warm_session` fork no live_runner (cria/reusa sessão com brief cacheado).
- **keepalive daemon** (`/keepalive`, cli.py:1770) — pinga a cada ~50min p/ manter
  TTL 1h vivo. **VERIFICAR amanhã:** está rodando? tem launchd? quem monitora se caiu?
  Roberto quer um monitoramento + disparo automático confiável, não "torcer pra estar on".
- Perguntas a responder: como sei que o cache está warm AGORA? existe métrica/health?
  o keepalive tem heartbeat observável? se a sessão warm expira, quem recria?

## 3. FIX SISTÊMICO JÁ APLICADO HOJE (2026-06-02) — não refazer
`cache_worker: true` desviava worker pro SDK Anthropic (API paga) → morria sem saldo
(`Error 400 credit balance too low`, model sonnet-4-6 via API). Verdade: CLI claude -p
já é warm-cacheado na assinatura → desligar cache_worker NÃO mata warm cache.
- Flipei `cache_worker: false` em 4 configs: antigravity (root), agilize, fw-social, fw-social-next.
- Já estavam false: burnless, forgetless, aeomachine, rudekwydra-atendimento.
- Capsule: `feedback-cache-worker-sdk-drena-api-vs-assinatura-2026-06-02`.

## 4. ENDURECIMENTO DURÁVEL PENDENTE (delegar ao gold)
`cached_worker` deveria detectar erro de billing/auth (400 credit / 401) e cair pro
`live_runner` CLI automaticamente, em vez de falhar duro. Assim nenhum projeto que
reabra `cache_worker: true` reabre a ferida. NÃO feito ainda.

## 6. BURNLESS DX — footguns que queimaram round-trips hoje (Roberto flagged)
Capsule: `feedback-burnless-dx-validacao-reativa-flags-2026-06-02`. Resumo:
- validação de path relativo é REATIVA (pós-dispatch) e pega o bloco ## Verify (falso
  positivo, não entende `cd`) → precisa --allow-relative-paths. 2 dispatches queimados.
- `--force` existe no `delegate` mas NÃO no `do` → argparse opaco. 1 dispatch queimado.
- Fix: erros acionáveis, flags consistentes do/delegate/run, --allow-relative-paths
  smarter (ignorar ## Verify / default quando absolutos), cheatsheet que o Maestro
  carregue ANTES de errar. Burnless cujo usuário é um Maestro LLM tem que ensinar o
  contrato proativamente, não punir reativamente.
- **FALSO-ERR do `## Verify` gate (bateu 4× hoje: d003, d007, d009 + 1)**: o gate
  re-executa o bloco ## Verify de um cwd que NÃO é a raiz do projeto (worker roda em
  cwd isolado, live_runner:432). Aí check com path relativo (`grep ... src/seo.ts`,
  `find html_puro`) falha com "No such file or directory" e marca **ERR mesmo com o
  trabalho 100% correto**. Até `cd projeto` no topo do bloco não salva (gate ignora/roda
  linha a linha?). Workaround atual: escrever TODO o ## Verify com paths ABSOLUTOS.
  Fix real: o gate rodar no cwd do projeto (root.parent), OU resolver paths relativos
  contra ele. Hoje custou 4 auditorias manuais que confirmaram OK falsamente-ERR. Isso
  corrói a confiança no exit-code (a doutrina P0 honest-exit-code depende do gate certo).

## 7. AEOMACHINE — revisar amanhã (Roberto pediu)
Banco multi-tenant `wzqdnhdemkvbjglhnyko` (service key em ~/.config/aeomachine/supabase.env).
3 tenants: `e396e36e…`=rudekwydra (132, JÁ consolidado hoje), `674217c2…`=234 artigos
(keywords eletroduto/eletrocalha → provável **Elecon**), `8d7935cb…`=89 (3º, confirmar).
- **(a) Elecon (674217c2, 234 artigos)**: Roberto diz "canibalismo agressivo" → rodar a MESMA
  auditoria/consolidação do rudekwydra (gold lê + plano canonical + force_sync). Maior volume.
- **(b) GATE anti-geração-futura (vale p/ TODOS tenants)**: a pipeline AINDA gera canibal novo.
  `autopilot-scheduler:263` só pula pauta `status='new'` duplicada; NÃO checa artigo PUBLICADO
  nem cluster_role. Falta gate: antes de gerar p/ keyword K, query articles publicados c/ K →
  vira satellite/leaf+canonical ou pula. É mudança de código (autopilot-scheduler/pautaDecision)
  + teste. Sem isso, a consolidação manual vira trabalho recorrente.
- Plano/comandos rudekwydra (template p/ Elecon): docs/blog-consolidacao-plano-2026-06-02.md.

## 5. rudekwydra-site — RESOLVIDO NA SESSÃO 2026-06-02 (não é pendência de amanhã)
Deploy dos 3 fixes SEO/AEO + expansão ABM foram feitos/deployados ainda na sessão de
02/06 (Roberto: "vamos matar isso já"). Branch `seo-aeo-improvements`.
- NOTA p/ depois (não bloqueia): prod estava À FRENTE do git (ABM + /en + info/ live
  mas não-commitados); capturei no commit `7456b13`. Vale endurecer o hábito
  buildar+deploy-sem-commit em algum momento.
