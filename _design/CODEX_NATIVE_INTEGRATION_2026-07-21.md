# Plano apertado — Burnless nativo no Codex

Data: 2026-07-21  
Status: proposta para execução  
Dependência: `ASK_CONTROL_PLANE_HARDENING_2026-07-21.md`

## Decisão

O Codex vira orquestrador de primeira classe do Burnless, com a mesma doutrina usada no Claude e integração própria — sem copiar cegamente a arquitetura Claude.

No Codex, a instrução durável canônica é `AGENTS.md`, não `CODEX.md`. A implantação terá quatro camadas:

1. `AGENTS.md` para a regra permanente;
2. skill local para o workflow reutilizável;
3. hooks para contexto, auditoria e enforcement possível;
4. plugin apenas quando o pacote estiver estável e distribuível.

## Contrato Codex ↔ Burnless

| Situação no Codex | Ação |
|---|---|
| Leitura/classificação barata | sidecar Bronze capaz |
| Implementação/testes/docs | Codex Silver via `burnless do/run` |
| Planejamento/arquitetura/arbitragem textual | `burnless ask --tier gold` |
| Decisão irreversível/segunda opinião final | `burnless ask --tier diamond`, explícito e único |
| Gold/Diamond precisam editar ou usar ferramentas | planejar com `ask`; executar separadamente em Silver; escalar executor só por exceção auditada |

Princípio: o host Codex mantém contexto, ferramentas e responsabilidade final. O worker devolve uma resposta ou artefato delimitado; não assume silenciosamente a sessão.

## Fase 1 — instrução e setup mínimos

Criar `burnless setup --codex` idempotente:

- instala um bloco gerenciado em `~/.codex/AGENTS.md`;
- oferece template de projeto `templates/codex/AGENTS.burnless.md`;
- preserva conteúdo do usuário fora dos marcadores;
- não cria `CODEX.md`;
- valida binário, versão, hooks, providers e tiers com `burnless doctor --codex`;
- imprime diff e suporta `--dry-run` antes de escrever.

Texto essencial do bloco:

> Antes de trabalho não trivial, classifique função, risco e necessidade de ferramentas. Use `burnless ask` para cognição Gold/Diamond e `burnless do/run` para execução. Não invoque diretamente modelos Gold/Diamond salvo bypass diagnóstico com motivo. Diamond é explícito, singular e nunca entra em retry automático.

Arquivos: novo `init_codex.py`, `setup_wizard.py`, `doctor.py`, `templates/codex/*`; testes de instalação, atualização e remoção idempotentes.

## Fase 2 — adapters Codex/OpenAI reais

Remover do `ask` qualquer fallback que trate provider desconhecido como Claude CLI. Criar adapters explícitos com contrato comum:

- `resolve()`;
- `explain()`;
- `invoke_text()`;
- `parse_usage()`;
- `capabilities()`;
- `cancel()`.

Reaproveitar o que já existe em `pilot/hosts/codex.py`, `warm_session_codex.py` e `cache_modes/codex_*`; não criar uma segunda pilha.

Suportar dois transportes separados:

- assinatura/CLI Codex não interativo;
- OpenAI API, quando configurada.

Ambos produzem `burnless.ask/v1`, registram tokens/cache/custo observados e propagam effort. Adicionar guard de recursão para impedir Codex → Burnless → Codex interativo → Burnless.

Arquivos: novo pacote `providers/`, adaptação de `pure_ask.py`, consolidação gradual dos adapters existentes; testes com fixtures de stream/JSON e sem rede.

## Fase 3 — skill Codex

Criar uma skill local `burnless-router` com gatilho para:

- tarefas não triviais;
- arquitetura, decisão e revisão de alto impacto;
- pedidos de comparação entre modelos;
- execução que poderia ser delegada a tier mais barato.

Fluxo da skill:

1. recuperar memória relevante quando configurada;
2. classificar função/risco/ferramentas;
3. rodar `burnless ask --dry-run` quando a rota não for óbvia;
4. usar o tier mínimo capaz;
5. verificar a saída contra arquivos/serviços vivos;
6. registrar decisão, custo e warning sem expor segredos.

A skill ensina; não finge enforcement. Publicar primeiro como skill local. Empacotar como plugin só após estabilidade e uso real.

## Fase 4 — hooks Codex

Usar os hooks nativos onde eles de fato cobrem o runtime:

- `SessionStart`: injeta status curto, projeto, política e memória relevante;
- `UserPromptSubmit`: adiciona lembrete de rota apenas em tarefas não triviais;
- `PreToolUse` para Bash: detecta invocação direta conhecida de modelo Gold/Diamond e bloqueia ou reescreve para Burnless;
- `PostToolUse`: coleta envelope/uso sem reenviar conteúdo sensível ao modelo;
- `PreCompact`/`PostCompact`: checkpoint e restauração do rolling context;
- `Stop`: exige apenas fechamento de telemetria pendente; nunca cria loop editorial genérico.

Limite explícito: hooks são guardrail, não fronteira completa. Chamadas hospedadas que não passam por ferramentas locais podem escapar; por isso `AGENTS.md`, skill e ledger continuam necessários.

Arquivos: `templates/codex/hooks/*`, configuração gerenciada e testes por evento com JSON fixtures.

## Fase 5 — plugin distribuível

Depois das fases 1–4:

- criar `.codex-plugin/plugin.json`;
- incluir skill, hooks e documentação;
- incluir MCP apenas se houver vantagem sobre a CLI; não duplicar `ask/do/status` por estética;
- publicar em marketplace local primeiro;
- instalar/reiniciar e validar em Codex CLI e desktop;
- manter fallback AGENTS + CLI para superfícies sem plugins, especialmente IDE.

O plugin distribui o protocolo; a CLI Burnless permanece a fonte de verdade.

## Fase 6 — memória e continuidade

Separar responsabilidades:

- Burnless epochs: contexto rolante da sessão e economia de prefixo;
- Forgetless: fatos duráveis entre projetos/sessões;
- Codex native transcript: evidência temporária, nunca API estável.

Não gravar transcript cru como memória permanente. `SessionStart`, compactação e `Stop` trocam envelopes pequenos, versionados e sem segredo.

## Fase 7 — validação e rollout

Matriz mínima de dogfooding:

- planejamento arquitetural;
- implementação média;
- auditoria de texto publicável;
- decisão Diamond singular;
- sessão longa com compactação;
- falha de provider e fallback;
- chamada direta proibida e bypass auditado.

Comparar em pelo menos três execuções por cenário:

- qualidade avaliada às cegas;
- tokens de entrada/saída/cache;
- custo exato/estimado;
- latência;
- número de chamadas caras;
- divergência entre rota prevista e efetiva.

Rollout: somente Roberto → dois projetos reais → default global. Bloqueio rígido de Gold vem depois de uma semana em modo warning sem falsos positivos críticos. Diamond pode ser rígido desde o início porque já é explicitamente opt-in.

## Gates de conclusão

- Nova sessão Codex lê a doutrina via `~/.codex/AGENTS.md`.
- Projeto pode sobrescrever a política com `AGENTS.override.md` sem editar o global.
- Gold/Diamond de texto passam por `ask`; execução normal permanece Silver.
- Provider Codex/OpenAI nunca cai silenciosamente no adapter Claude.
- Hooks não expõem segredos e não dependem do formato instável do transcript.
- Todas as chamadas aparecem no ledger unificado.
- Setup/upgrade/uninstall são idempotentes e têm `--dry-run`.
- Fluxo funciona sem plugin; plugin apenas melhora instalação e distribuição.

## Referências oficiais verificadas

- [AGENTS.md: descoberta global e por projeto](https://developers.openai.com/codex/guides/agents-md)
- [Hooks: lifecycle, bloqueio e limites de cobertura](https://developers.openai.com/codex/hooks)
- [Skills](https://developers.openai.com/codex/skills)
- [Plugins: empacotamento e superfícies suportadas](https://developers.openai.com/codex/plugins)

