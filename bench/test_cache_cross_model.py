#!/usr/bin/env python3
"""Teste empírico: o cache da Anthropic é compartilhado entre modelos?

Hipótese A (cache isolado por modelo):
  Sonnet cria cache → Haiku não lê → cache_read=0 no Haiku

Hipótese B (cache compartilhado):
  Sonnet cria cache → Haiku lê o mesmo cache → cache_read>0 no Haiku

Uso:
  ANTHROPIC_API_KEY=sk-ant-... python bench/test_cache_cross_model.py
"""

import os
import sys
import anthropic

SYSTEM_BLOCK = [
    {
        "type": "text",
        "text": (
            "You are a test assistant. This system prompt is intentionally long "
            "to exceed the 1024-token cache minimum required by the Anthropic API. "
            + ("X " * 600)  # ~1200 tokens para garantir threshold
        ),
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }
]

MODELS = {
    "sonnet": "claude-sonnet-4-6",
    "haiku":  "claude-haiku-4-5-20251001",
}


def call(client: anthropic.Anthropic, model: str, label: str) -> dict:
    resp = client.messages.create(
        model=model,
        system=SYSTEM_BLOCK,
        messages=[{"role": "user", "content": "respond with exactly one word: pong"}],
        max_tokens=5,
        extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"},
    )
    u = resp.usage
    result = {
        "model":        model,
        "label":        label,
        "input":        getattr(u, "input_tokens", 0),
        "output":       getattr(u, "output_tokens", 0),
        "cache_write":  getattr(u, "cache_creation_input_tokens", 0)
                        or getattr(u, "cache_creation_input_tokens_1h", 0),
        "cache_read":   getattr(u, "cache_read_input_tokens", 0),
        "text":         resp.content[0].text if resp.content else "",
    }
    return result


def main():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("Precisa de ANTHROPIC_API_KEY")

    client = anthropic.Anthropic(api_key=key)

    print("=" * 60)
    print("Teste: cache cross-model Anthropic")
    print("=" * 60)

    results = []

    # 1. Sonnet — cria o cache (write esperado)
    print("\n[1] Sonnet — primeira chamada (cache write esperado)...")
    r = call(client, MODELS["sonnet"], "sonnet-1st")
    results.append(r)
    print(f"    cache_write={r['cache_write']}  cache_read={r['cache_read']}")

    # 2. Sonnet — segunda chamada (cache read esperado, confirma que cache funciona)
    print("\n[2] Sonnet — segunda chamada (cache read esperado)...")
    r = call(client, MODELS["sonnet"], "sonnet-2nd")
    results.append(r)
    print(f"    cache_write={r['cache_write']}  cache_read={r['cache_read']}")

    # 3. Haiku — mesma chamada com o MESMO system block
    #    Se cache_read > 0 → cache é compartilhado entre modelos (Hipótese B)
    #    Se cache_read = 0 → cache é isolado por modelo (Hipótese A)
    print("\n[3] Haiku — mesma chamada (hipótese B: cache_read>0 se compartilhado)...")
    r = call(client, MODELS["haiku"], "haiku-1st")
    results.append(r)
    print(f"    cache_write={r['cache_write']}  cache_read={r['cache_read']}")

    # 4. Haiku — segunda chamada (confirma se Haiku tem cache próprio)
    print("\n[4] Haiku — segunda chamada...")
    r = call(client, MODELS["haiku"], "haiku-2nd")
    results.append(r)
    print(f"    cache_write={r['cache_write']}  cache_read={r['cache_read']}")

    # Veredicto
    haiku_1st = next(r for r in results if r["label"] == "haiku-1st")
    sonnet_2nd = next(r for r in results if r["label"] == "sonnet-2nd")

    print("\n" + "=" * 60)
    print("VEREDICTO")
    print("=" * 60)

    if sonnet_2nd["cache_read"] == 0:
        print("⚠️  Sonnet 2ª chamada não leu cache — talvez TTL muito curto ou erro.")
        print("   Resultado inconclusivo.")
    elif haiku_1st["cache_read"] > 0:
        print("✅ HIPÓTESE B CONFIRMADA: cache é COMPARTILHADO entre modelos.")
        print("   Haiku leu o cache criado pelo Sonnet.")
        print("   → Ping keepalive com Haiku renova TTL do cache do Sonnet.")
    else:
        print("✅ HIPÓTESE A CONFIRMADA: cache é ISOLADO por modelo.")
        print("   Haiku cache_read=0 — criou cache próprio.")
        print("   → Ping keepalive com Haiku NÃO renova TTL do cache do Sonnet.")

    print("\nTabela completa:")
    print(f"{'label':<14} {'model':<30} {'write':>7} {'read':>7}")
    print("-" * 60)
    for r in results:
        print(f"{r['label']:<14} {r['model']:<30} {r['cache_write']:>7} {r['cache_read']:>7}")


if __name__ == "__main__":
    main()
