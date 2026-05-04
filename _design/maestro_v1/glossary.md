# Burnless Glossary v1

## Como o brain/worker usam isto

Este bloco é injetado no `system` com `cache_control: ephemeral 1h` e é **byte-idêntico**
em toda chamada (Brain e Worker compartilham → mesmo cache prefix).

Termos são marcadores semânticos densos. O LLM aprende no few-shot que falar em
glossário é o protocolo nativo do Burnless. Texto fora do glossário é permitido
mas marcado como "raw:" pra clareza.

---

## Tiers (bandas de qualidade/custo)

- `gld` = gold (maior qualidade/custo, definido pelo usuário)
- `slv` = silver (trabalho diário, validação, código médio)
- `brz` = bronze (encoder/decoder, classificação, clean, local/cheap)

## Estados de tarefa

- `OK` = sucesso completo, validado
- `PART` = parcial (alguns gates falharam mas progresso real)
- `BLK` = blocked (faltou input/recurso/permissão)
- `ERR` = erro (subprocess crash, timeout, exceção)
- `WIP` = em andamento

## Fluxo Burnless

- `cap` = capsule (mensagem comprimida no protocolo glossário)
- `exec` = execution log (detalhe completo, fora do cache do Brain)
- `del` = delegation (tarefa atribuída a um worker)
- `ref` = referência a arquivo/log/capsule (`ref:exec/T42`)
- `aud` = auditoria (Brain solicita expandir uma ref)
- `hd` = header (glossário + role, cache compartilhado)

## Ações comuns

- `imp` = implementar
- `val` = validar (build/test/smoke)
- `aud` = auditar (ver ação acima — também sentido de "investigar")
- `fix` = corrigir
- `rev` = revisar
- `del→T` = delegar tarefa T
- `ret` = retornar capsule pro Brain

## Tenant glossary (extensão configurável)

Este bloco do glossário core NÃO contém termos de domínio (projetos, frameworks,
vocabulário do cliente). Esses entram via **tenant glossary**, uma feature
do produto Burnless onde cada usuário/empresa cadastra seus próprios termos.

Localização: `~/.burnless/tenant_glossary.yaml` (per-user) ou
`.burnless/tenant_glossary.yaml` (per-project, override).

Schema:
```yaml
version: 1
terms:
  - short: auth
    full: authentication subsystem
  - short: dash
    full: customer-facing dashboard
  # ... etc
```

Target: o loader combina `glossary.md` (core, byte-idêntico)
+ tenant_glossary (variável). Apenas o core entra no bloco de cache
compartilhável. Tenant glossary é cacheado por projeto/tenant.

Nota de implementação: versões antigas detectam tenant glossary, mas ainda não
fazem merge completo. O roadmap do protocolo move isso para `core + tenant +
session`.

Razão: core é vocabulário do framework (tier/cap/del/exec). Tenant é vocabulário
do negócio. Misturar quebra cache cross-tenant e contamina o produto com
termos de um cliente específico.

## Session emergent glossary

Além do core e do tenant glossary, o protocolo deve manter um glossário vivo da
sessão. A LLM compressora pode propor deltas, mas o Burnless valida antes de
aceitar:

```text
GLOSSARY_DELTA
auth = authentication service
wkr = worker
cap = capsule
END_GLOSSARY_DELTA
```

Esse log deve ser append-only e sobreviver à compactação como
`GLOSSARY_SUPERBLOCK`, separado do `CAPSULE_SUPERBLOCK`.

## Padrão de capsule

Formato canônico de uma capsule do Brain ou Worker:

```
{tier} {action} {target} :: {status} {summary} [refs:...]
```

Exemplos:

```
slv imp app/auth :: OK schema+router+prompts ok, build pass [ref:exec/T42]
gld del→T43 slv val app/auth :: aud smoke
brz enc usr-msg :: OK 412→38 tok
slv imp app/dash :: ERR conflito schema [ref:exec/T44]
gld aud T44 :: precisa rebase schema antes
```

## Marcadores especiais

- `?` no fim = brain pede confirmação ao humano
- `!` no fim = urgente, brain pede atenção
- `~` antes do tier = degradar (ex: `~gld` = ia ser gold mas degradou pra silver)
- `+` antes do tier = upgrade (ex: `+slv` = era bronze, virou silver via filter)

## Anti-padrões (NÃO fazer)

- Não escrever texto longo em capsule. Se precisa expandir, gravar em exec_log e referenciar.
- Não inventar termos novos sem registrar em tenant/session glossary.
- Não misturar PT-BR e glossário na mesma capsule. Glossário é protocolo IA-pra-IA.
- Output pro humano (depois do decoder Haiku) é PT-BR natural. Esse glossário NUNCA aparece pro humano.
