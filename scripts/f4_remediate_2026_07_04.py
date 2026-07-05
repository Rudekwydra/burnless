import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/Users/roberto/antigravity/burnless/src")
from burnless import recovery  # noqa: E402

ROOT = "/Users/roberto/antigravity/burnless"
HOST = "claude"
STALE_SIDS = [
    "15028a72-45ef-49f8-81d1-3b7a31c0ac70",
    "c39b6536-fd42-462b-a982-f45bd452e9e3",
    "ac7ae44a-9095-46d9-8328-1716fc048418",
    "5b0921db-426f-413a-8731-49ff021a0b65",
    "dcd80363-fbb4-4946-a204-28811999c6c0",
    "6fa209e7-0cbd-4861-8040-028d4bb76ee3",
    "cccbc397-109f-42f7-b68f-d523d322ab75",
    "be414c6a-f69a-42b3-b9f5-17c75dc14d21",
    "cfb5b944-7446-44b0-b1ed-69d19414037f",
]

root_path = Path(ROOT)
report = []

for sid in STALE_SIDS:
    checkpoint = recovery.read_checkpoint(root_path, HOST, sid)
    if checkpoint is None:
        report.append(f"{sid}: SKIP (no checkpoint found)")
        continue

    process_instance_id = checkpoint.get("process_instance_id") or sid
    journal_head_before = int(checkpoint.get("journal_head") or 0)
    applied_before = int(checkpoint.get("applied_through") or 0)
    gen_before = int(checkpoint.get("generation") or 0)
    living_chars_before = len((checkpoint.get("living_md") or ""))

    backed_up = []
    for p in list(recovery._checkpoint_paths(root_path, HOST, sid)) + list(recovery._mirror_paths(root_path, HOST, sid)):
        if p.exists():
            backup = p.with_name(p.name + ".corrupt-2026-07-04")
            shutil.copy2(p, backup)
            backed_up.append(str(backup))

    committed = recovery.write_checkpoint(
        root_path,
        host=HOST,
        host_session_id=sid,
        process_instance_id=process_instance_id,
        living_md="",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
        journal_head=journal_head_before,
    )

    report.append(
        f"{sid}: gen {gen_before}->{committed.get('generation')} | "
        f"living_md_chars {living_chars_before}->0 | applied_through {applied_before}->0 | "
        f"journal_head preserved={journal_head_before} | backups={len(backed_up)}"
    )

print("\n".join(report))