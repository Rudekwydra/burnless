# Briefing — auditoria profunda do Burnless (Codex GPT-5.5)

## Papel
Você é uma segunda opinião adversarial sobre o PRÓPRIO framework Burnless — o orquestrador
multi-tier que está te despachando agora mesmo (você é literalmente um worker dele nesta run).
Isso é uma vantagem: você está rodando dentro do mesmo tipo de caminho (`codex exec`) que teve
2 bugs reais encontrados e corrigidos hoje mesmo nesta sessão. Sua missão é achar MAIS bugs,
inconsistências e footguns antes que a gente precise descobrir na marra de novo.

## Contexto: 2 bugs já corrigidos hoje (não repita esses achados, use como padrão do que procurar)

1. `/Users/roberto/antigravity/burnless/src/burnless/live_runner.py` — `run_with_live_panel()`
   injetava argumentos de warm-session do codex (`--cd`, `--skip-git-repo-check`,
   `--ignore-user-config`, `--ignore-rules`) direto no comando base SEM chamar
   `_dedup_valueless_flags()`, causando `--skip-git-repo-check` duplicado e o codex CLI recusando
   o comando inteiro (`argument cannot be used multiple times`). A função irmã em
   `/Users/roberto/antigravity/burnless/src/burnless/agents.py`
   (`_inject_warm_fork_args()`) já fazia isso certo — `live_runner.py` tinha uma
   REIMPLEMENTAÇÃO PARALELA da mesma lógica, sem herdar a correção.
2. Mesma função em `live_runner.py`: um bloco "bare-equivalent flags" injetava flags exclusivas
   do Claude (`--no-session-persistence`, `--strict-mcp-config`, `--disable-slash-commands`,
   `--exclude-dynamic-system-prompt-sections`, `--setting-sources`) em QUALQUER worker,
   incondicionalmente — inclusive codex, que rejeita todas essas flags. Essas são exatamente as
   flags que `agents.py::_strip_claude_only_flags()` já trata como claude-only — só que
   `live_runner.py` nunca chamava essa função nem checava o provider antes de injetar.

**Padrão-raiz por trás dos 2 bugs**: `live_runner.py` duplica lógica que já existe (corrigida,
testada) em `agents.py`, ao invés de reusá-la. Isso é uma classe de risco: procure por MAIS
lugares onde a mesma lógica (provider detection, flag injection, warm-session handling, dedup)
aparece implementada em mais de um arquivo com potencial de divergir.

## Outro bug encontrado (side-effect, ainda não corrigido — pra você avaliar se vale a pena)

Ao rodar `burnless do --tier gold --gold codex:gpt-5.5 "..."` no projeto aeomachine, a flag
`--gold PROVIDER:MODEL` (documentada no `--help` como "override the gold worker for this run
only") **persistiu** no arquivo `.burnless/config.yaml` DAQUELE PROJETO — trocando o `gold` padrão
de `opus` pra `gpt-5.5`/codex permanentemente, até alguém notar e reverter manualmente. Isso
contradiz a documentação ("this run only"). Procure em
`/Users/roberto/antigravity/burnless/src/burnless/config.py` e nos comandos que processam
`--gold`/`--silver`/`--bronze`/`--diamond` (provavelmente em
`/Users/roberto/antigravity/burnless/src/burnless/cli.py`) onde esse override é aplicado, e
diga: (a) é intencional (grava no config por design) ou é bug; (b) se for bug, onde exatamente
o código deveria estar escrevendo num objeto de config in-memory/temporário em vez de persistir
no arquivo.

## O que fazer

Faça uma auditoria ampla — não se limite aos exemplos acima, eles são só o "tipo" de coisa que
queremos que você procure. Leia o código real em
`/Users/roberto/antigravity/burnless/src/burnless/` (foco nos arquivos centrais de execução:
`agents.py`, `live_runner.py`, `cli.py`, `config.py`, `spec_validator.py`, `routing.py`,
`warm_session_codex.py`, e qualquer `warm_session_*.py` irmão para os outros providers) e ataque
nestes ângulos:

1. **Paridade entre providers**: toda lógica que existe pro path `claude` também existe (ou é
   corretamente ausente) pro path `codex`, `gemini`, `ollama`? Onde há assimetria não-documentada?
2. **Duplicação de lógica entre arquivos**: onde a mesma responsabilidade (detecção de provider,
   injeção de flag, dedup, warm-session) está implementada em mais de um lugar com risco de
   divergir quando um for corrigido e o outro não?
3. **Contratos de "override efêmero" que na verdade persistem**: qualquer flag/opção documentada
   como "só para esta run" que na prática grava em disco/config — o achado do `--gold` acima é
   um exemplo, procure outros (`--bronze`, `--silver`, `--diamond`, `--cold-cache`, etc.).
4. **Retry cego em erro determinístico**: onde o código de retry (`[retry] dXXX: prev=ERR`) tenta
   de novo um comando que vai falhar de forma IDÊNTICA (erro de parse de CLI, binário ausente,
   erro de sintaxe) em vez de reconhecer que não é transitório. Procure a lógica de retry em
   `live_runner.py`/`cli.py` e veja se distingue erro determinístico de transitório.
5. **Testes que cobrem a função isolada mas não a fiação**: onde existe um teste unitário bonito
   pra uma função helper, mas nada testa que ela é REALMENTE chamada no caminho de execução real
   (esse foi exatamente o gap dos 2 bugs de hoje — `_dedup_valueless_flags` tinha testes
   perfeitos, só não era chamada onde devia).
6. **Qualquer outro bug, inconsistência, ou débito técnico** que você achar navegando o código,
   mesmo fora dos ângulos acima.

## Formato de saída — grave em disco, não despeje tudo no chat
Escreva a auditoria completa em:
`/Users/roberto/antigravity/burnless/docs/audits/2026-07-02-codex-deep-audit-RESULTADO.md`

Estrutura sugerida: lista de achados, cada um com severidade (crítico/importante/menor), evidência
(arquivo + linha), e recomendação concreta de fix. Ordene por severidade.

Na resposta final ao Maestro, retorne APENAS:
- Confirmação do path acima
- Quantos achados por severidade (ex: "3 críticos, 5 importantes, 4 menores")
- Os 3 achados mais críticos, em 1 linha cada

## PROIBIÇÕES DURAS
- NÃO edite nenhum arquivo de código do Burnless — isto é auditoria read-only em Markdown.
- NÃO rode `burnless do`/`burnless delegate`/qualquer comando que dispare uma NOVA delegação —
  você pode rodar `pytest`, `grep`, `git log`, leitura de arquivos, mas não invocar o próprio CLI
  do burnless recursivamente.
- NÃO repita os 2 achados já corrigidos (dedup de flag / claude-only flags) como se fossem novos —
  cite-os só como contexto se relevante, mas não os liste como "achado" na sua auditoria.
- NÃO despeje a auditoria completa na saída do worker — só no arquivo RESULTADO.

## Verify
```sh
test -f /Users/roberto/antigravity/burnless/docs/audits/2026-07-02-codex-deep-audit-RESULTADO.md
grep -qi "crítico\|critico\|importante\|menor" /Users/roberto/antigravity/burnless/docs/audits/2026-07-02-codex-deep-audit-RESULTADO.md
```
