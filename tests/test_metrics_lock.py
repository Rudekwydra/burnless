import threading
from pathlib import Path
from burnless.metrics import record, bump_legacy_counter, load


def test_record_concurrent_no_lost_updates(tmp_path):
    """8 threads × 25 record calls should accumulate to 200 tokens."""
    metrics_path = tmp_path / "metrics.json"
    audit_path = tmp_path / "audit.jsonl"

    def worker():
        for _ in range(25):
            record(
                metrics_path,
                audit_path,
                source="capsule_compression",
                amount=1,
                reason="test",
            )

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final_metrics = load(metrics_path)
    assert final_metrics["burnless_tokens"] == 200


def test_bump_legacy_concurrent(tmp_path):
    """4 threads × 25 bump_legacy_counter calls should accumulate to 100."""
    metrics_path = tmp_path / "metrics.json"

    def worker():
        for _ in range(25):
            bump_legacy_counter(metrics_path, "legacy_run_calls", 1)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final_metrics = load(metrics_path)
    assert final_metrics["legacy_run_calls"] == 100
