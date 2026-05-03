# Burnless Glossary v1

## Como o brain/worker usam isto

Este bloco é injetado no `system` com `cache_control: ephemeral 1h` e é **byte-idêntico**
em toda chamada (Brain e Worker compartilham → mesmo cache prefix).

Termos são marcadores semânticos densos. O LLM aprende no few-shot que falar em
glossário é o protocolo nativo do Burnless. Texto fora do glossário é permitido
mas marcado como "raw:" pra clareza.

---

## Tiers (modelos)

- `dia` = diamond / codex (execução de código, sandbox workspace-write)
- `gld` = gold / opus (Brain default; raciocínio/orquestração)
- `slv` = silver / sonnet (worker padrão; doc, codigo médio, validação)
- `brz` = bronze / haiku (encoder/decoder, classificação, clean)

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

O loader concatena `glossary.md` (core, byte-idêntico cross-tenant)
+ tenant_glossary (variável). Apenas o core entra no `cache_control` ephemeral
compartilhado entre tenants. Tenant glossary é cacheado per-tenant.

Razão: core é vocabulário do framework (tier/cap/del/exec). Tenant é vocabulário
do negócio. Misturar quebra cache cross-tenant e contamina o produto com
termos de um cliente específico.

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
- Não inventar termos novos sem registrar aqui (glossary versionado quebra cache).
- Não misturar PT-BR e glossário na mesma capsule. Glossário é protocolo IA-pra-IA.
- Output pro humano (depois do decoder Haiku) é PT-BR natural. Esse glossário NUNCA aparece pro humano.
