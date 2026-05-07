"""filter_entrada_spike — local LLM + deterministic squeeze compresses user messages
before reaching cloud LLM.

Two-stage pipeline (the v2 thesis):
  homem -> [Stage 1: LLM filter, semantic] -> [Stage 2: regex squeeze, deterministic] -> LLM cloud

Stage 1 (LLM): Llama/Qwen via Ollama drops filler, keeps intent. Cost: zero (local).
Stage 2 (regex): abbreviation dict + whitespace collapse. Cost: microseconds.
  Inspired by LLMLingua (Jiang et al., Microsoft 2024) — LLMs are robust to
  degraded text because they were trained on code (no spaces), CJK (no spaces),
  and chat shorthand ("u", "&", "w/"). Common abbreviations preserved.

Setup:
    ollama pull qwen2.5:7b-instruct   # or llama3.1:8b; ~5GB, one-time
    ollama serve                       # if not running

Usage:
    python bench/filter_entrada_spike.py --model qwen2.5:7b-instruct

Output (per sample): orig → LLM-filtered → squeezed, with token count + ratio at each stage.

Cost: zero (local). No API key, no quota consumption.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "llama3.1:8b"

_PROMPT_BASE = """ROLE: You are a text compressor. Input: a verbose user message inside <message> tags. Output: a JSON object with one field, `compressed`, containing the minimal version that preserves every intent, constraint, name, and number.

KEEP: action verbs, nouns, file paths, numbers, library/tool/model names, error messages, hard constraints, urgency markers that affect the action
DROP: greetings, pleasantries, hedging, repetition, pure emotion words, backstory, parenthetical asides, filler

OUTPUT RULES (strict):
- Return a single JSON object: {{"compressed": "<one-line compressed text>"}}
- No commentary outside the JSON, no preamble, no markdown fences
- The `compressed` value is one line, written in {lang_name}, no quotes inside
- If the input is already minimal, `compressed` is identical to the input
- If you find yourself writing "the user" inside `compressed` — STOP, you are summarizing, which is wrong

EXAMPLES:

{examples}

NOW COMPRESS:

<message>{message}</message>
"""

_EXAMPLES_EN = """<message>hi please write a python script that reads data.csv and prints rows where age > 30, thanks!</message>
{"compressed": "write python script: read data.csv, print rows where age > 30"}

<message>fix the bug in auth.py login flow</message>
{"compressed": "fix bug in auth.py login flow"}

<message>I was thinking maybe we should refactor the authentication module because it's a bit messy, what do you think? no rush</message>
{"compressed": "refactor authentication module"}"""

_EXAMPLES_PT = """<message>oi por favor implementa o teste de cache no claude -p mas com haiku primeiro pra economizar quota, valeu!</message>
{"compressed": "implementa teste de cache no claude -p, usa haiku primeiro"}

<message>arruma o bug no auth.py no fluxo de login</message>
{"compressed": "arruma bug em auth.py fluxo de login"}

<message>olha eu tava pensando aqui sei lá, talvez a gente devesse refatorar o módulo de autenticação porque tá meio bagunçado, o que acha? sem pressa</message>
{"compressed": "refatorar módulo de autenticação"}"""

_LANG_NAMES = {"en": "English", "pt": "Portuguese"}
_EXAMPLES_BY_LANG = {"en": _EXAMPLES_EN, "pt": _EXAMPLES_PT}

# Lightweight prompt variant — minimal rules + 1 example. Tests if less constraint
# yields more aggressive compression (small models sometimes over-comply with strict rules).
_PROMPT_LIGHT_PT = """Comprima a mensagem do usuário para o mínimo possível. Mantenha ações, nomes, números, paths. Solte saudações, hedging, emoção. Responda APENAS com JSON: {{"compressed": "..."}}

Exemplo:
<message>oi por favor implementa o teste de cache no claude -p mas com haiku primeiro pra economizar quota, valeu!</message>
{{"compressed": "implementa teste cache claude -p, haiku primeiro"}}

<message>{message}</message>
"""

_PROMPT_LIGHT_EN = """Compress the user message to the minimum. Keep actions, names, numbers, paths. Drop greetings, hedging, emotion. Respond ONLY with JSON: {{"compressed": "..."}}

Example:
<message>hi please implement the cache test on claude -p but use haiku first to save quota, thanks!</message>
{{"compressed": "implement cache test claude -p, haiku first"}}

<message>{message}</message>
"""

_PROMPT_LIGHT_BY_LANG = {"en": _PROMPT_LIGHT_EN, "pt": _PROMPT_LIGHT_PT}

# Ultra-aggressive variant — pushes the model to drop more than the light prompt.
# Goal: find the empirical ceiling of compression that still preserves intent.
# The model is told to keep ONLY verbs, nouns, paths, numbers — and to delete
# every transition word, modifier that's not a hard constraint, and explanatory
# clause. Targets 4×+ ratio.
_PROMPT_ULTRA_PT = """Comprima ao MÁXIMO ABSOLUTO. Mantenha SÓ:
- Verbos de ação
- Substantivos (objetos, ferramentas, arquivos, libs)
- Números e paths
- Constraints duros (versões, quantidades, prazos críticos)

DELETE:
- Conjunções, preposições, artigos
- Modificadores que não são constraints duros
- Cláusulas explicativas, contextuais, justificativas
- Qualquer "porque", "para que", "considerando"
- Pontuação além de vírgulas

Saída: JSON {{"compressed":"..."}} numa única linha. Sem code fences. Sem prosa.

Exemplo:
<message>oi por favor implementa o teste de cache no claude -p mas com haiku primeiro pra economizar quota, valeu!</message>
{{"compressed":"implementa teste cache claude -p, haiku primeiro"}}

<message>preciso urgente que voce escreva um script python que leia um csv chamado dados.csv com colunas nome idade salario e calcule a media de salario por faixa etaria</message>
{{"compressed":"script python lê dados.csv (nome,idade,salario), média salario por faixa etária"}}

<message>{message}</message>
"""

_PROMPT_ULTRA_EN = """Compress to ABSOLUTE MAXIMUM. Keep ONLY:
- Action verbs
- Nouns (objects, tools, files, libs)
- Numbers and paths
- Hard constraints (versions, quantities, critical deadlines)

DELETE:
- Conjunctions, prepositions, articles
- Modifiers that aren't hard constraints
- Explanatory, contextual, justification clauses
- Any "because", "in order to", "considering"
- Punctuation beyond commas

Output: JSON {{"compressed":"..."}} single line. No code fences. No prose.

Example:
<message>hi please implement the cache test on claude -p but use haiku first to save quota, thanks!</message>
{{"compressed":"implement cache test claude -p, haiku first"}}

<message>I urgently need you to write a python script that reads a csv called data.csv with columns name age salary and computes mean salary per age bracket</message>
{{"compressed":"python script reads data.csv (name,age,salary), mean salary per age bracket"}}

<message>{message}</message>
"""

_PROMPT_ULTRA_BY_LANG = {"en": _PROMPT_ULTRA_EN, "pt": _PROMPT_ULTRA_PT}

# Pivot-EN: input may be Portuguese, output is FORCED to English.
# Empirical motivation (cl100k_base, May 2026):
#   "implement" 1 tok < "implementa" 2 tok < "实现" 2 tok
#   "urgent"    1 tok < "紧急"        4 tok
# English is the densest BPE language in cl100k_base — translating during
# compression buys an extra ~10-30% on top of pure squeeze.
_PROMPT_PIVOT_EN = """Compress AND translate to English. Input may be in any language; output MUST be English.

Keep: action verbs, nouns, file paths, numbers, library/tool names, hard constraints
Drop: greetings, hedging, repetition, filler, modifiers that aren't constraints
Translate proper nouns/identifiers as-is (file names, library names, URLs stay verbatim).

Output: JSON {{"compressed":"<one-line English>"}}. No code fences. No prose.

Examples:

<message>oi por favor implementa o teste de cache no claude -p mas com haiku primeiro pra economizar quota, valeu!</message>
{{"compressed":"implement cache test on claude -p, use haiku first"}}

<message>preciso urgente refatorar a classe UserManager porque tá com 800 linhas, separar em UserService, UserRepository, UserValidator</message>
{{"compressed":"urgent: refactor UserManager (800 LOC) into UserService, UserRepository, UserValidator"}}

<message>encontrei um bug na função de upload — quando o arquivo passa de 50MB o nginx cortou a conexão antes de chegar no app</message>
{{"compressed":"bug: upload >50MB, nginx drops connection before reaching app"}}

<message>{message}</message>
"""


def build_filter_prompt(lang: str, message: str, style: str = "full") -> str:
    if lang not in _EXAMPLES_BY_LANG:
        raise ValueError(f"unsupported lang: {lang}. Supported: {list(_EXAMPLES_BY_LANG)}")
    if style == "pivot_en":
        return _PROMPT_PIVOT_EN.format(message=message)
    if style == "ultra":
        return _PROMPT_ULTRA_BY_LANG[lang].format(message=message)
    if style == "light":
        return _PROMPT_LIGHT_BY_LANG[lang].format(message=message)
    return _PROMPT_BASE.format(
        lang_name=_LANG_NAMES[lang],
        examples=_EXAMPLES_BY_LANG[lang],
        message=message,
    )

SAMPLES_PT = [
    # — Pedidos técnicos médios (5) —
    "preciso que você crie uma função em python que receba uma lista de números e retorne a mediana, mas tem que tratar lista vazia retornando None",
    "implementa um endpoint REST em flask que receba JSON com nome e email, valida o email com regex, e salva no banco SQLite",
    "escreve um script bash que faça backup da pasta /var/log compactado em tar.gz com timestamp no nome do arquivo",
    "cria uma função javascript que filtra um array de objetos pelo campo ativo igual a true e ordena por data decrescente",
    "configura o pgbouncer entre o app e o postgres, modo transaction pooling, max connections 100, monitora pelo Datadog",
    # — Pedidos verbose com background (4) —
    "olha, eu preciso urgente refatorar essa classe UserManager porque ela tá com mais de 800 linhas, tem responsabilidade demais, e os testes estão acoplados na implementação interna, podia separar em UserService, UserRepository e UserValidator",
    "tava pensando aqui e acho que a gente devia migrar do REST pra GraphQL porque hoje o frontend faz N requests pra montar uma tela, e isso tá pesando no mobile principalmente em conexões 3G fracas",
    "sei lá se isso faz sentido mas seria legal ter uma feature flag pra ligar/desligar o novo sistema de pagamento gradualmente, primeiro pra 1% dos users, depois 10%, depois 50%, e ver se quebra alguma coisa antes de full rollout",
    "estou trabalhando num sistema de processamento de pedidos e-commerce que recebe quinhentos pedidos por minuto em pico de Black Friday, atualmente tudo passa por uma fila Redis com workers em python, mas tá começando a perder mensagens quando os workers caem, queria uma solução mais resiliente",
    # — Discussão arquitetural (3) —
    "qual é o trade-off entre usar Redis com persistência AOF vs RDB pra cache de sessão num app que tem 10k users ativos por dia?",
    "vale a pena separar o serviço de notificações em microservice próprio ou manter monolito por enquanto, considerando que o time tem 4 devs?",
    "se a gente usar Kafka pra event streaming, qual seria o problema de processar mensagens fora de ordem em transações financeiras?",
    # — Bug reports (3) —
    "encontrei um bug na função de upload — quando o arquivo passa de 50MB o nginx cortou a conexão antes de chegar no app, mas não tem mensagem de erro pro usuário, só fica girando",
    "o login tá falhando intermitentemente em 5 por cento dos casos, log mostra timeout na chamada pro Active Directory mas só acontece entre 14h e 16h",
    "a query do dashboard tá demorando 8 segundos pra carregar agora, antes era 2 segundos, parece que faltou índice em algum lugar depois da última migration",
    # — Comandos com filler emocional (3) —
    "por favor, urgentíssimo, joga fora todos esses arquivos .pyc da pasta src, tá lotando o repo e o flake8 reclama",
    "ai meu deus que código horroroso, refatora isso aqui pelo amor de deus, separa em métodos menores no mínimo",
    "obrigado se você puder dar uma olhada rápida no PR número 423, tá esperando review desde ontem e o time tá bloqueado",
    # — Refatoração/cleanup (3) —
    "remove todos os console.log e print statements espalhados pelo código em prod, tem uns 80 lugares pelo grep",
    "renomeia a variável data pra event_data em toda a base, ela tá ambígua porque tem outra data que é date",
    "extrai aquele bloco repetido de 30 linhas que aparece em 4 arquivos pra uma função utilitária em utils/formatters.py",
    # — Dúvidas (3) —
    "vale a pena migrar nosso CI do Jenkins pro GitHub Actions, considerando que já temos 200 jobs configurados em Jenkins?",
    "qual é a melhor estratégia de feature flag pra A/B testing em mobile, considerando que o release leva 2 semanas pra chegar a 100 por cento dos users?",
    "como vocês fazem rollback de migration de banco em prod sem downtime, especialmente em Postgres com tabelas grandes?",
    # — Pedido longo migração (1) —
    "temos um app legado em PHP 5.6 com mysql 5.5 que precisa migrar pra PHP 8 e mysql 8, o problema é que ele tem 200k linhas, depende de mcrypt que não existe mais, e usa funções deprecated pra todo lado, qual é o caminho menos doloroso?",
    # — DevOps/ops (2) —
    "atualiza o helm chart do nginx-ingress pra versão 4.8, mantém a config de rate limiting que está em values.yaml",
    "cria um cron job no kubernetes que rode todo dia às 3h da manhã apagando dados de auditoria com mais de 90 dias",
    # — Mensagens curtas-médias (3) —
    "atualiza a documentação do README com o novo endpoint /api/v2/orders",
    "adiciona testes unitários pra função calculate_discount em tests/test_pricing.py",
    "sobe o PR de fix do bug do login intermitente, já tá pronto e testado",
    # — Frases longas e complexas (20 extra, total 50) —
    "a equipe de produto pediu pra adicionar uma feature de exportação em CSV das ordens dos últimos 30 dias com filtros por status, valor mínimo, e cliente, e que o arquivo seja gerado assíncrono porque pode demorar e mandar email com link",
    "o servidor de produção tá com latência alta no endpoint /api/orders desde ontem às 22h, o New Relic mostra que 95 por cento do tempo é gasto em uma query SQL que faz join entre orders customers products payments, deve ter perdido um índice em algum lugar",
    "preciso de uma análise de tradeoffs entre Postgres com particionamento por mês vs MongoDB sharded por customer_id, considerando que vamos crescer de 10M registros agora pra 1B em 18 meses, com leituras dominantes 10 pra 1 e queries com filtros complexos",
    "vamos abrir o source code do projeto X mas precisa primeiro fazer auditoria pra remover qualquer credencial hardcoded, comentário interno, referências a clientes, dados de teste com PII, configurações de produção, tudo que possa expor a empresa",
    "o cliente Banco Itaú pediu uma demo do produto na próxima quinta-feira, mas o feature de relatórios não tá pronto ainda, qual seria o caminho mais rápido pra mostrar algo funcional sem prometer demais e sem expor o estado real",
    "refatorar o módulo de pricing que tem 5 estratégias de desconto (percentual, valor fixo, BOGO, escalada por quantidade, sazonal) atualmente todas em uma classe gigante de 1500 linhas, separar em strategies pattern com testes unitários cobrindo todos os casos",
    "a equipe de SRE notificou que estamos no top 3 maiores consumidores de Kubernetes na empresa mas com baixa eficiência (CPU médio 12%, memória 30%), pediu otimização, pode ser HPA ajustado, requests/limits revistos, ou mudança de instance types",
    "investigar por que o app mobile iOS tá crashando aleatoriamente em 2 por cento das sessões depois do release 4.8.0, sem stack trace claro no Crashlytics, suspeitamos de race condition no novo SDK de pagamento da Stripe que adicionamos",
    "propor arquitetura para sistema de notificações multi-canal (email via SendGrid, SMS via Twilio, push via Firebase, WhatsApp via Twilio) com retry, deduplicação, rate limit por canal, e fallback automático se um canal falhar",
    "o legacy admin panel feito em PHP 5 com jQuery precisa migrar pra Vue 3 com TypeScript em ondas, mantendo todas as funcionalidades, e os usuários internos não podem ser afetados durante a transição que vai durar 4 meses",
    "escrever um script Python que monitore um diretório S3, baixe arquivos novos, processe com pandas (limpeza de nulos, normalização de timestamps, deduplicação por hash), e suba o resultado em outro bucket S3 com naming convention YYYY-MM-DD-source.parquet",
    "como fazer CI/CD para projetos com múltiplos serviços compartilhando libs comuns, hoje temos 12 microservices em monorepo, todos consomem 3 libs internas, e o pipeline tá lento porque rebuilda tudo mesmo quando muda só um serviço",
    "o time de marketing pediu que a landing page tenha A/B test de 4 variações de copy ao mesmo tempo, com tracking de conversão por segmento (origem do tráfego, geolocação, device), e precisa subir essa semana",
    "vamos negociar contrato com fornecedor de cloud, eles oferecem 30 por cento de desconto se commitarmos 3 anos, mas a gente tá num momento de avaliar multi-cloud, qual a melhor estratégia pra não ficar locked-in mas aproveitar o desconto",
    "alguém sabe por que o GitHub Actions tá demorando 25 minutos pra rodar testes que rodavam em 8 minutos mês passado, não mudamos o número de testes, talvez seja o ambiente node 20 vs 18, ou algum runner instável",
    "preciso revisar o PR número 1247 que adiciona suporte a OAuth2 no nosso SaaS, autor é júnior, primeira contribuição grande, código tá funcional mas com vários antipatterns que vão atrapalhar manutenção, qual a melhor forma de dar feedback construtivo",
    "o CTO pediu uma estimativa de quanto tempo levaria pra reescrever o monolito Ruby on Rails (8 anos, 200k LOC, 4M users ativos) em microservices, considerando time atual de 15 devs e que a operação não pode parar",
    "propor estratégia de observabilidade pra app multi-tenant que serve 500 clientes, cada um com SLA diferente, precisamos saber quem tá afetado quando algo quebra mas sem ter 500 dashboards diferentes",
    "como migrar usuários ativos de autenticação local (bcrypt) pra OAuth com Google e GitHub sem forçar reset de senha em massa, mantendo continuidade de UX e sem perder dados de sessão ativa",
    "o sistema de cobrança recorrente tá falhando silenciosamente em ~3 por cento das transações Stripe, o webhook de payment_failed não tá disparando porque o endpoint retorna 500 quando o customer não existe mais no banco mas ainda existe no Stripe",
]

SAMPLES_EN = [
    "could you please implement the cache test on claude -p, but run it with Haiku first to save quota?",
    "I'm feeling kind of stuck right now... but if we think it through, we can probably make this work by "
    "filtering before the expensive LLM... my idea is human → filter → LLM → filter back into the human's "
    "tone... the LLM doesn't need all the verbose blah blah...",
    "fix this mess, I'm in mourning",
    "I urgently need you to write a python script that reads a csv called data.csv with columns name age "
    "salary and calculates the average salary per age bracket (young up to 30, adult 30-60, senior 60+) "
    "and saves the result in a new csv called report.csv with columns bracket and average. it has to run "
    "with pandas. thanks!",
]

_SAMPLES_BY_LANG = {"en": SAMPLES_EN, "pt": SAMPLES_PT}


def call_ollama(prompt: str, model: str) -> str:
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",  # Ollama JSON mode — forces output to be valid JSON
    })
    proc = subprocess.run(
        ["curl", "-sS", OLLAMA_URL, "-d", payload, "-H", "Content-Type: application/json"],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ollama HTTP call failed: {proc.stderr[:300]}")
    body = proc.stdout.strip()
    if not body:
        raise RuntimeError("empty response from ollama (is `ollama serve` running?)")
    try:
        outer = json.loads(body)
        inner_str = outer["response"].strip()
        # Tolerate markdown code fences (some models wrap JSON despite format=json)
        if inner_str.startswith("```"):
            inner_str = re.sub(r"^```(?:json)?\s*\n?", "", inner_str)
            inner_str = re.sub(r"\n?```\s*$", "", inner_str).strip()
        inner = json.loads(inner_str)
        val = inner.get("compressed", "")
        # Some models (e.g. ministral) return a structured object; coerce to string
        if isinstance(val, dict) or isinstance(val, list):
            val = json.dumps(val, ensure_ascii=False, separators=(",", ":"))
        return str(val).strip()
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError(f"unexpected ollama response: {body[:300]}") from exc


try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")  # GPT-4 BPE; close proxy for Claude

    def approx_tokens(text: str) -> int:
        return max(1, len(_enc.encode(text)))
    _TOKEN_MODE = "tiktoken (cl100k_base)"
except ImportError:
    def approx_tokens(text: str) -> int:
        return max(1, len(text) // 4)  # heuristic fallback — char/4
    _TOKEN_MODE = "heuristic (char/4) — install tiktoken for real counts"


# Stage 2: telegrafista — drop articles, prepositions, fillers that are 1-BPE-token in
# cl100k_base / Claude tokenizers and disappear cleanly when removed. NO abbreviations
# (those ADD tokens — empirically validated 2026-05-06: "thx" = 2t > "thank you" = 2t,
# "w/" = 2t > "with" = 1t, "vc" = 1t = "você" = 2t but only saves on rare words).
# Strategy: drop, do not abbreviate.
TELEGRAFISTA_STOPWORDS = {
    # Portuguese — articles, common prepositions, fillers
    "o", "a", "os", "as", "um", "uma", "uns", "umas",
    "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas",
    "que", "por", "para", "pra", "com", "e", "é",
    # English — articles, common prepositions, fillers
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "and", "or",
    "is", "are", "was", "were", "be", "been", "by", "with", "as", "that",
}


def deterministic_squeeze(text: str) -> str:
    """Stage 2: telegrafista — drop filler words (articles, common preps), collapse whitespace.
    Deterministic, microseconds. NO abbreviations (BPE breaks "thx"/"w/" into MORE tokens than
    "thank you"/"with"). Empirically validated: telegrafista yields 1.1-1.3x token reduction
    in cl100k_base on PT and EN; abbreviations were yielding 0.85-0.95x (LOSS).
    """
    words = text.split()
    kept = [w for w in words if w.strip(".,!?;:").lower() not in TELEGRAFISTA_STOPWORDS]
    out = " ".join(kept)
    # Collapse whitespace and redundant punctuation
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"\.{2,}", ".", out)
    out = re.sub(r",{2,}", ",", out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--lang", default="en", choices=sorted(_EXAMPLES_BY_LANG.keys()),
                    help="prompt + sample language (en or pt). User picks per project.")
    ap.add_argument("--squeeze-stage", default="pre", choices=["pre", "post", "both", "none"],
                    help="when to apply deterministic regex squeeze")
    ap.add_argument("--prompt-style", default="full", choices=["full", "light", "ultra", "pivot_en"],
                    help="full = 4 examples + strict rules; light = 1 example + minimal; ultra = aggressive; pivot_en = compress AND translate to English (densest in cl100k_base)")
    args = ap.parse_args()

    print(f"filter_entrada_spike using {args.model} via {OLLAMA_URL}  (lang={args.lang}, squeeze={args.squeeze_stage})")
    print(f"tokens: {_TOKEN_MODE}")
    print(f"(setup: `ollama pull {args.model}` then `ollama serve`)\n")

    samples = _SAMPLES_BY_LANG[args.lang]
    apply_pre = args.squeeze_stage in ("pre", "both")
    apply_post = args.squeeze_stage in ("post", "both")

    total_orig = 0
    total_final = 0
    failed_samples = 0
    for i, msg in enumerate(samples, 1):
        pre_in = deterministic_squeeze(msg) if apply_pre else msg
        prompt = build_filter_prompt(args.lang, pre_in, style=args.prompt_style)
        try:
            llm_out = call_ollama(prompt, args.model)
        except Exception as exc:  # noqa: BLE001 — fail-open per-sample, don't kill run
            print(f"[{i}] WARN: LLM failed ({str(exc)[:100]}); passthrough", file=sys.stderr)
            llm_out = pre_in
            failed_samples += 1
        final = deterministic_squeeze(llm_out) if apply_post else llm_out

        t_orig = approx_tokens(msg)
        t_pre = approx_tokens(pre_in)
        t_llm = approx_tokens(llm_out)
        t_final = approx_tokens(final)
        total_orig += t_orig
        total_final += t_final

        r_final = t_orig / t_final if t_final > 0 else float("inf")
        savings = (t_orig - t_final) * 3.0 / 1_000_000  # Sonnet input price

        print(f"[{i}] orig={t_orig:>3}t -> pre={t_pre:>3}t -> llm={t_llm:>3}t -> final={t_final:>3}t ({r_final:.1f}x)  saved=${savings:.6f}")
        print(f"     IN:    {msg[:120]}{'...' if len(msg) > 120 else ''}")
        if apply_pre:
            print(f"     PRE:   {pre_in[:120]}{'...' if len(pre_in) > 120 else ''}")
        print(f"     LLM:   {llm_out[:120]}{'...' if len(llm_out) > 120 else ''}")
        if apply_post:
            print(f"     FINAL: {final[:120]}{'...' if len(final) > 120 else ''}")
        print()

    r_total = total_orig / total_final if total_final > 0 else float("inf")
    total_savings = (total_orig - total_final) * 3.0 / 1_000_000
    fail_note = f" [{failed_samples}/{len(samples)} samples passthrough due to LLM errors]" if failed_samples else ""
    print(f"OVERALL: orig={total_orig}t -> final={total_final}t ({r_total:.1f}x)  savings=${total_savings:.6f}{fail_note}")

    # Save full results JSON for cross-model aggregation
    out_dir = Path.home() / ".burnless" / "test_data" / "filter_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_model = args.model.replace("/", "_").replace(":", "-")
    out_file = out_dir / f"{safe_model}_{args.lang}_{args.squeeze_stage}_{args.prompt_style}.json"
    out_file.write_text(json.dumps({
        "model": args.model, "lang": args.lang, "squeeze_stage": args.squeeze_stage,
        "prompt_style": args.prompt_style, "samples_count": len(samples),
        "total_orig": total_orig, "total_final": total_final, "ratio": r_total,
        "savings_usd": total_savings,
    }, indent=2))
    print(f"saved: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
