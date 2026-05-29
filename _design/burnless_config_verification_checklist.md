# Burnless — checklist de verificação de configuração correta (passo a passo)
# Use no pós-reset. Cada item: O QUE + COMO. Marcar [ok]/[x] ao verificar. NUNCA confiar em OK de worker sem verificar fonte.

## A. Integridade de versão / instalação
- [ ] A1 versão única = 0.9.0  →  `burnless --version` == `grep __version__ src/burnless/__init__.py` == pyproject dynamic
- [ ] A2 binário aponta pro dev tree  →  `python3 -c "import burnless,os;print(os.path.dirname(burnless.__file__))"` == ~/antigravity/burnless/src/burnless
- [ ] A3 suite verde  →  `python3 -m pytest tests/ -q --ignore=tests/benchmarks` (esperado ~348 passed)

## B. Auth / billing (o que quebrou hoje)
- [ ] B1 sem ANTHROPIC_API_KEY no env/rc  →  `env | grep ANTHROPIC` vazio; `grep ANTHROPIC ~/.zshrc` vazio
- [ ] B2 worker strip da chave  →  `grep -n "pop(\"ANTHROPIC_API_KEY\"" src/burnless/agents.py src/burnless/live_runner.py`
- [ ] B3 worker haiku/sonnet roda na ASSINATURA  →  `burnless do --tier bronze "echo teste"` retorna OK
- [ ] B4 **gold=opus headless**: confirmar se ainda bate API ("credit balance") OU se assinatura cobre  →  `burnless do --tier gold "echo teste"`. Se falhar 400 → gold-worker indisponível, rotear design via sonnet/opus-interativo. DECIDIR: trocar gold tier p/ sonnet?

## C. Per-layer tier config
- [ ] C1 preset + modelos resolvidos  →  `python3 -c "from burnless import config as c; from pathlib import Path; print(c.resolve_layer_models(c.load(Path('.burnless/config.yaml'))))"` (esperado encoder/maestro)
- [ ] C2 worker tiers  →  `grep -A1 "gold:\|silver:\|bronze:" .burnless/config.yaml` (qual modelo cada um resolve)
- [ ] C3 compression mode  →  `grep -A1 compression .burnless/config.yaml` == light (e nos outros 6 projetos)

## D. Isolamento das 3 camadas (paridade) — matriz
Verificar cada flag em encoder(hook) / maestro(maestro_runner.build_command) / worker(live_runner):
- [ ] D1 --no-session-persistence  (hoje: enc NAO, mae NAO, wrk SIM)
- [ ] D2 --strict-mcp-config        (hoje: enc NAO, mae NAO, wrk SIM)
- [ ] D3 --disable-slash-commands   (hoje: enc NAO, mae NAO, wrk SIM)
- [ ] D4 --exclude-dynamic-system-prompt-sections (hoje: enc NAO, mae SIM, wrk SIM)
- [ ] D5 --setting-sources project,local (hoje: enc NAO, mae SIM, wrk SIM)
- [ ] D6 encoder usa --system-prompt? (hoje NAO — instrução vai no user message; precisa slot p/ prefixo cacheável)
  COMO: `grep -oE '\-\-[a-z-]+' templates/hooks/burnless_compact_haiku.sh | sort -u`  +  `grep -E '"--' src/burnless/maestro_runner.py`  +  worker log flags

## E. Cache de prefixo por camada (o teste decisivo)
- [ ] E1 tamanho do prefixo de cada camada vs threshold (haiku 2048 / opus-sonnet 1024)
  COMO: medir tokens do system prompt de cada camada (maestro 522 tok, warm brief 121 tok, encoder ~79 tok — todos sub-2048 hoje)
- [ ] E2 cacheia de verdade? (cold-probe nonce: 2 calls IDÊNTICAS, 2a deve ter cache_read>0 / cache_creation~0)
  COMO: rodar a mesma call 2x com mesmo prefixo; se cache_creation>0 na 2a e cache_read=0 → NAO cacheia (sub-threshold)
- [ ] E3 medir SEMPRE com nonce p/ custo cold real (evitar contaminação tipo chat-em-haiku)

## F. Preâmbulo compartilhado (a dívida)
- [ ] F1 existe módulo de preâmbulo ≥2048 compartilhado? (hoje NAO — só chat_mode._CACHE_PAD, órfão)
  COMO: `grep -rn "preamble\|shared.prefix\|moral.block\|2048" src/burnless/*.py | grep -v bak`
- [ ] F2 encoder+maestro+worker usam o MESMO prefixo byte-idêntico? (hoje 3 prompts standalone)

## G. No-leak (privacidade)
- [ ] G1 maestro NAO carrega CLAUDE.md/raw  →  inspecionar build_command + system prompt (cwd=/tmp, --system-prompt fixo)
- [ ] G2 worker brief project-agnostic, sem conversa/secrets  →  `python3 -c "from burnless import warm_session as w; print(w.build_project_brief('.'))"`
- [ ] G3 --add-dir ~/.claude no worker dá ACESSO de leitura (hardening: avaliar remover)

## H. Stack de instrução do worker
- [ ] H1 warm brief (warm_session) — texto correto, role-neutral
- [ ] H2 --append-system-prompt WORKER_MODE — "task spec é a única regra, não ler CLAUDE.md, emitir envelope"
- [ ] H3 spec da delegation chega como user message

## I. Roteamento (correção do maestro)
- [ ] I1 telegramas representativos roteiam pro tier certo (plan→gold, spec→silver/bronze, done→done, trivial→reply, prod/secret→ask_user)
  COMO: rodar maestro_runner.run_maestro em set fixo (com nonce p/ cold), conferir decisão. NAO decidir haiku-vs-opus default sem teste limpo (n>=15).

## J. Doutrina (já feito hoje, re-confirmar)
- [ ] J1 PROTOCOL.md = fonte única; zero "Brain" residual em código/docs live
- [ ] J2 CLAUDE.md global + soul + nutri apontam pra docs/COMMANDS.md + PROTOCOL.md
