# Spine proxy — running it for real (Mac runbook)

The synthetic benchmarks are in `bench/`. This runbook is about the number
they cannot produce: what the **provider itself** reports after a day of
your real sessions — above all `cache_read_input_tokens`, the proof that
the append-only spine keeps the prefix warm instead of fighting the cache.

## 1. Install and start

```bash
cd ~/path/to/burnless
git checkout claude/headroom-rolling-memory-wtxxut
pip install -e .

cd ~/your-real-project      # a project with .burnless/ (run `burnless init` if new)
burnless proxy              # foreground; state lands in .burnless/proxy/
```

It prints the port and the export line. In a second terminal:

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8787"
claude          # or codex, cursor, aider — anything that honors the env var
```

Then just work. Turn 1 passes through untouched; from turn 2 on the spine
builds itself. Nothing else to configure.

## 2. Watch it engage

```bash
burnless proxy stats            # what the rolling memory did, plus provider usage
burnless proxy stats --json     # same, machine-readable
tail -f .burnless/proxy/ledger.jsonl
```

`kind: "request"` rows are the transform's own view (`absorbed`,
`tokens_saved_approx`, `bytes_in/out`). `kind: "usage"` rows are the
provider's numbers, read out of the response in flight.

What good looks like after a real session:

- `spine applied` climbing, `deepest spine` growing one capsule per turn
- `cache read` a large share of `prompt billed` — that is the cache-warm
  claim, measured, not argued
- `cache written` small and roughly constant per turn (the delta: one new
  capsule), not proportional to history

Early turns report `below_regime` — expected: under 3 completed exchanges
the spine is overhead, so it stays out of the way.

## 3. Inspect and recover

```bash
burnless proxy show <ref>          # capsule + full verbatim exchange
burnless retrieve --query "cache"  # searches spine capsules AND delegation evidence
burnless retrieve --query "cache" --full
ls .burnless/proxy/capsules | wc -l
```

Every capsule ends in `[ref:<hash>]`; the verbatim is at
`.burnless/proxy/exchanges/<hash>.json`. Nothing is destroyed — read a
capsule you think is wrong and compare against its original.

## 4. Knobs (`.burnless/config.yaml`)

```yaml
proxy:
  port: 8787
  keep_tail_exchanges: 1     # completed exchanges kept VERBATIM (continuity reserve)
  min_exchanges: 3           # regime guard: short sessions never engage
  codec: extractive          # or "ollama" for the local model (extractive fallback)
  max_capsule_chars: 700
  cache_breakpoint: true     # ephemeral marker on the last spine block
privacy:
  raw_retention: plain       # "none" → verbatim never touches disk (capsules only)
```

Raising `keep_tail_exchanges` to 2–3 trades savings for a longer verbatim
window if capsules feel too lossy while the codec is still the extractive
floor.

## 5. Judging capsule quality

The extractive codec is the floor, not the goal. After a session, read a
dozen capsules:

```bash
for f in .burnless/proxy/capsules/*.txt; do echo "--- $f"; cat "$f"; done | less
```

The test is not "is it pretty" but: **would this line let a cold reader
follow the conversation?** Where it fails, that is the case for the LLM
codec (`codec: ollama`, or a bronze tier), not for carrying more verbatim.

## 6. If something looks wrong

The proxy is fail-open by construction: any surprise forwards the original
request untouched, so the worst case is no compression, never a broken
conversation. To confirm it is out of the path, unset `ANTHROPIC_BASE_URL`
and compare. Ledger rows with `reason` explain every passthrough
(`below_regime`, `no_capsules_yet`, `not_smaller`, `error:*`).
