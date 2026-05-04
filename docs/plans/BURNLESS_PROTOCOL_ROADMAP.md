# Burnless Protocol Roadmap

Este plano organiza o Burnless como protocolo aberto e separa o que deve ser
open source do que deve virar Burnless Cloud/Enterprise.

## Positioning

Burnless is a candidate protocol layer for human-LLM and LLM-LLM communication:
a living, compressed, privacy-aware intermediary language between humans,
maestros, and workers.

Em termos práticos, o Burnless deve separar quatro superfícies:

1. o que o humano escreve;
2. o que o maestro precisa entender;
3. o que o provedor de LLM pode ver;
4. o que o projeto precisa lembrar, auditar ou destruir.

## Product Boundary

### Open Source Protocol

O repositório aberto deve conter o protocolo e uma implementação local honesta:

- formato de cápsula;
- modos de privacidade;
- compressão e minificação;
- glossário emergente;
- `burnkey` local;
- testes de reversibilidade/irreversibilidade local;
- documentação clara sobre limites técnicos.

O objetivo do open source é criar confiança, adoção e padrão de mercado.

### Cloud/Enterprise

O produto pago deve monetizar operação e responsabilidade:

- custódia de chaves com KMS/HSM;
- RBAC, SSO e aprovação em duas etapas;
- trilhas de auditoria;
- retenção configurável;
- legal hold;
- relatórios de destruição de chave;
- SIEM/webhooks;
- políticas por workspace, projeto e usuário;
- suporte, SLA e governança.

Empresas não pagam pelo botão `burnkey`; pagam pela prova operacional de que a
política foi aplicada corretamente.

## Burnkey

`burnkey` é a operação explícita de destruir a chave local de uma sessão ou
cápsula.

Sem outra cópia da chave, sem raw transcript retido e sem backup aplicável, a
cápsula criptografada se torna irrecuperável pelo Burnless.

### OSS Semantics

Implementar no protocolo aberto:

- comando `/burnkey` no chat;
- comando CLI `burnless burnkey`;
- destruição da chave em keyring local;
- marcação da sessão como queimada;
- falha clara ao tentar decodificar cápsula queimada;
- confirmação explícita antes da operação;
- testes provando que a keyring local não consegue recuperar o conteúdo.

### Enterprise Semantics

Reservar para produto pago:

- destruição em KMS/HSM;
- políticas de aprovação;
- prova auditável de destruição;
- retenção seletiva por compliance;
- legal hold que bloqueia burnkey;
- relatórios exportáveis;
- integração com governança corporativa.

### Claim Seguro

Usar este tipo de frase:

> Burnkey destroys the local decryption key. When no other copy of the key or
> raw source is retained, the encrypted capsule becomes unrecoverable by
> Burnless.

Evitar:

- "anonimização automática";
- "zero knowledge" antes de existir arquitetura completa;
- "enterprise-grade encryption" enquanto o envelope ainda for experimental.

## Emergent Glossary

O glossário emergente é uma parte central do protocolo. Ele cria uma linguagem
intermediária viva, compactada e cacheável entre humano, maestro e workers.

### Layer 1: Core Glossary

Glossário fixo, versionado e cacheável:

- termos do protocolo;
- nomes de campos;
- tipos de cápsula;
- comandos;
- tiers `gold`, `silver`, `bronze`;
- regras de compactação.

Este bloco deve mudar pouco. Quando muda, a versão do protocolo muda.

### Layer 2: Tenant/Project Glossary

Glossário controlado pelo usuário, projeto ou empresa:

- nomes de sistemas;
- siglas internas;
- produtos;
- times;
- termos de domínio.

No OSS, pode viver em `.burnless/glossary.yaml`.

No Enterprise, deve ter governança, escopo, revisão e auditoria.

### Layer 3: Session Emergent Glossary

Glossário vivo da sessão. A LLM compressora pode propor novos mapeamentos, mas
o Burnless deve validar antes de aceitar.

Exemplo de delta:

```text
GLOSSARY_DELTA
auth = authentication service
wkr = worker
cap = capsule
END_GLOSSARY_DELTA
```

Validações mínimas:

- não colidir com mapeamento existente;
- não mapear segredo, token, CPF, email ou dado sensível em texto claro;
- a expansão precisa aparecer no contexto recente;
- a abreviação precisa ser curta e estável;
- o mapeamento precisa ser útil em turns futuros;
- o delta deve ser append-only.

## Cache Layout

O prompt cache deve ser organizado em blocos estáveis:

```text
[protocol header]
[core glossary]
[tenant/project glossary]
[frozen session glossary blocks]
[frozen capsule blocks]
[hot glossary deltas]
[hot capsule tail]
[new user message]
```

Blocos antigos devem permanecer byte-identical sempre que possível. O hot tail
pode mudar; os blocos congelados precisam preservar cache hit.

## Cache-Aware Compaction

Compactar cápsulas e glossário como se fossem a mesma coisa é erro de protocolo.

A compactação deve gerar dois superblocos:

```text
GLOSSARY_SUPERBLOCK
CAPSULE_SUPERBLOCK
```

Regras:

- o glossário não pode ser perdido durante compactação;
- mapeamentos usados continuam preservados;
- mapeamentos aposentados precisam ser marcados, não esquecidos silenciosamente;
- o cálculo de ROI deve considerar economia futura de cache read;
- se houver risco de colisão ou perda semântica, não compactar o glossário.

## Privacy Modes

### Cost

Default atual. Reduz custo e repetição de contexto. Não promete privacidade
forte.

### Redact

Substitui valores sensíveis localmente antes de qualquer chamada externa.

```text
Roberto, CPF 123... -> PERSON_1, TAX_ID_1
```

O mapa fica local.

### Audit

Mantém chaves em keystore local ou Enterprise KMS. Permite revisão posterior.

### Opaque

Mantém chaves somente em memória. Sessões antigas ficam intencionalmente
indecodificáveis quando a chave desaparece.

### Burnkey

Operação explícita para destruir a chave antes do fim natural da sessão.

## Implementation Order

1. Corrigir documentação antiga do glossário e remover termos obsoletos como
   `diamond`/`dia`.
2. Fazer `glossary_loader.py` carregar core + tenant/project glossary de fato.
3. Criar `session_glossary.py` com log append-only e validação de deltas.
4. Ensinar o Brain/encoder a pedir e capturar `GLOSSARY_DELTA`.
5. Incluir blocos de glossário no cache layout.
6. Alterar compactação para preservar `GLOSSARY_SUPERBLOCK`.
7. Implementar `/burnkey` e `burnless burnkey` local.
8. Adicionar testes de privacidade, colisão, cache e irreversibilidade local.
9. Atualizar README, PROTOCOL, VISION, site e PyPI.
10. Só então publicar claims mais fortes de protocolo.

## Done Criteria

O Burnless estará pronto para divulgação pública mais agressiva quando:

- README não tiver promessa maior do que a implementação;
- `PROTOCOL.md` documentar modos, cápsulas, glossário e burnkey;
- site explicar OSS vs Enterprise sem ambiguidade;
- testes provarem que v2 não embute chave;
- testes provarem que `burnkey` quebra decode local;
- glossário emergente sobreviver à compactação;
- raw transcript tiver política explícita de retenção;
- o PyPI estiver na versão correspondente ao repositório;
- tag Git e site estiverem publicados.

## Near-Term Message

Mensagem segura para o primeiro post, começando pela dor que vende:

> Burnless is an open protocol experiment for human-LLM and LLM-LLM
> communication. It turns long conversations into compact capsules, keeps a
> cache-warm operational language between maestros and workers, and is evolving
> toward local-key privacy modes like audit, opaque, and burnkey.

Trocar por:

> Burnless is 88% cheaper at turn 10 because it turns quadratic LLM context
> replay into linear capsule memory. Under the hood, it is evolving into an
> open protocol layer for human-LLM and LLM-LLM communication: a living,
> compressed, privacy-aware language between humans, maestros, and workers.

Versão curta:

> 88% cheaper at turn 10. Burnless turns long LLM conversations into compact
> capsule memory, then routes work across any Maestro and any Worker.

Essa ordem é mais forte para divulgação: economia primeiro, protocolo depois.
O protocolo é o moat; a economia é o gatilho de adoção.
