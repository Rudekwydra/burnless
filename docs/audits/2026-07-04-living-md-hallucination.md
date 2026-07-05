# Auditoria — living_md alucinado, remediação F4

**Data:** 2026-07-04/05 · **Escopo:** F4 do plano em `docs/plans/2026-07-04-living-md-hallucination-fix.md`

## O que foi feito

- Sweep em todas as sessões do projeto (`grep -rl "PERGUNTA:\|RESPOSTA:\|RESPOSTA FINAL\|SÍNTESE\|Qual ordem de prioridade" .burnless/epochs/sessions/claude/*/checkpoint.json`) achou **10 sessões contaminadas**: as 9 listadas abaixo + `fee436ab-0873-47ff-b4d7-43fc78580248`.
- Cruzamento com processos `burnless pty` vivos (`ps aux`) mostrou que `fee436ab` (pid `host-22689`) e `990025db-8075-4af4-ac9c-93e727997ad1` (pid `host-76725`, a sessão corrente) têm processo ativo associado — ficaram **fora de escopo** por decisão do Roberto (não mexer em abas em uso).
- As **9 sessões sem processo vivo** foram resetadas (`living_md=""`, `harvested_state` vazio, `applied_through=0`, `journal_head` preservado) via `write_checkpoint`, num script mecânico (`scripts/f4_remediate_2026_07_04.py`) despachado como bronze:
  - `15028a72-45ef-49f8-81d1-3b7a31c0ac70`
  - `c39b6536-fd42-462b-a982-f45bd452e9e3`
  - `ac7ae44a-9095-46d9-8328-1716fc048418`
  - `5b0921db-426f-413a-8731-49ff021a0b65`
  - `dcd80363-fbb4-4946-a204-28811999c6c0`
  - `6fa209e7-0cbd-4861-8040-028d4bb76ee3`
  - `cccbc397-109f-42f7-b68f-d523d322ab75`
  - `be414c6a-f69a-42b3-b9f5-17c75dc14d21`
  - `cfb5b944-7446-44b0-b1ed-69d19414037f`
- Reset confirmado independentemente (leitura direta dos 9 `checkpoint.json` no disco, não só relato do worker): `living_md` vazio em todos, `fee436ab`/`990025db` sem alteração.

## Gap conhecido — backup de evidência falhou

O script pedia backup (`checkpoint.json.corrupt-2026-07-04`) de cada checkpoint antes do reset. **O backup falhou silenciosamente nos 9** — `p.exists()` retornou `False` porque o script chamou `recovery._checkpoint_paths(root, ...)`/`_mirror_paths(root, ...)` passando a raiz do projeto (`/Users/roberto/antigravity/burnless`) sem normalizar para `.burnless/` primeiro. Essas duas funções, ao contrário de `read_checkpoint`/`write_checkpoint`, **não chamam `_root_path()` internamente** — esperam receber o root já resolvido. Resultado: checou existência num caminho sem o segmento `.burnless/`, sempre falso, zero cópias feitas (confirmado no stdout do próprio script: `backups=0`).

Efeito prático: o texto fictício desses 9 checkpoints foi sobrescrito sem cópia preservada. Como é conteúdo fabricado (não é dado real de projeto perdido), o risco é baixo — mas registra-se aqui como achado honesto, não maquiado como sucesso total (delegação ficou `PART` nos dois runs, exatamente por causa desse gap na verificação).

**Nota de engenharia:** essa é uma pegadinha real no código do Burnless — `_checkpoint_paths`/`_mirror_paths` (recovery.py) exigem root pré-normalizado, enquanto `read_checkpoint`/`write_checkpoint` normalizam sozinhos via `_root_path()`. Qualquer script futuro que combine as duas famílias de função precisa chamar `_root_path()` explicitamente antes de usar as funções de path cru.

## Pendências

- `burnless pilot doctor` listar runs vivos com idade/PID pra facilitar identificar sessões órfãs no futuro (produto, não crítico).
- F1/F2/F3 do plano de fix já implementados e testados (ver commit correspondente) — processos `burnless pty` já em execução (fee436ab, 990025db) só recebem o fix quando reiniciados manualmente pelo Roberto.
- F6 item 5 (cenário golden harness "compactor malicioso") não coberto ainda.
