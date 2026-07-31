#!/usr/bin/env python3
import json
import subprocess
import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict


def run_gh_api(path, extra_args=None):
    """Run gh api and return parsed JSON or None on error."""
    cmd = ["gh", "api"]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(path)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(f"Warning: gh api {path} failed: {result.stderr.strip()}", file=sys.stderr)
            return None
        return json.loads(result.stdout)
    except Exception as e:
        print(f"Warning: {path} error: {e}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="Rudekwydra/burnless")
    parser.add_argument("--out-dir", default="/Users/roberto/antigravity/burnless/_ops/traffic")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    snapshots_dir = out_dir / "snapshots"
    daily_file = out_dir / "daily.jsonl"

    snapshots_dir.mkdir(parents=True, exist_ok=True)

    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot_file = snapshots_dir / f"{today_utc}.json"

    data = {}

    clones_data = run_gh_api(f"repos/{args.repo}/traffic/clones")
    if clones_data:
        data["clones"] = clones_data

    views_data = run_gh_api(f"repos/{args.repo}/traffic/views")
    if views_data:
        data["views"] = views_data

    referrers_data = run_gh_api(f"repos/{args.repo}/traffic/popular/referrers")
    if referrers_data:
        data["referrers"] = referrers_data

    paths_data = run_gh_api(f"repos/{args.repo}/traffic/popular/paths")
    if paths_data:
        data["paths"] = paths_data

    stargazers_data = run_gh_api(
        f"repos/{args.repo}/stargazers",
        ["-H", "Accept: application/vnd.github.star+json", "--paginate"]
    )
    if stargazers_data:
        data["stargazers"] = stargazers_data

    with open(snapshot_file, "w") as f:
        json.dump(data, f, indent=2)

    records_by_key = {}

    if "clones" in data and isinstance(data["clones"], dict):
        clones_list = data["clones"].get("clones", [])
        for item in clones_list:
            if "timestamp" in item:
                date = item["timestamp"][:10]
                key = f"clones:{date}"
                records_by_key[key] = {
                    "key": key,
                    "metric": "clones",
                    "date": date,
                    "count": item.get("count", 0),
                    "uniques": item.get("uniques", 0),
                    "label": None
                }

    if "views" in data and isinstance(data["views"], dict):
        views_list = data["views"].get("views", [])
        for item in views_list:
            if "timestamp" in item:
                date = item["timestamp"][:10]
                key = f"views:{date}"
                records_by_key[key] = {
                    "key": key,
                    "metric": "views",
                    "date": date,
                    "count": item.get("count", 0),
                    "uniques": item.get("uniques", 0),
                    "label": None
                }

    if "referrers" in data and isinstance(data["referrers"], list):
        for item in data["referrers"]:
            referrer = item.get("referrer", "")
            key = f"referrers:{today_utc}:{referrer}"
            records_by_key[key] = {
                "key": key,
                "metric": "referrers",
                "date": today_utc,
                "count": item.get("count", 0),
                "uniques": item.get("uniques", 0),
                "label": referrer
            }

    if "paths" in data and isinstance(data["paths"], list):
        for item in data["paths"]:
            path = item.get("path", "")
            key = f"paths:{today_utc}:{path}"
            records_by_key[key] = {
                "key": key,
                "metric": "paths",
                "date": today_utc,
                "count": item.get("count", 0),
                "uniques": item.get("uniques", 0),
                "label": path
            }

    stars_by_date = defaultdict(lambda: {"count": 0})
    if "stargazers" in data and isinstance(data["stargazers"], list):
        for item in data["stargazers"]:
            if "starred_at" in item:
                date = item["starred_at"][:10]
                stars_by_date[date]["count"] += 1

    for date, counts in stars_by_date.items():
        key = f"stars:{date}"
        records_by_key[key] = {
            "key": key,
            "metric": "stars",
            "date": date,
            "count": counts["count"],
            "uniques": counts["count"],
            "label": None
        }

    if daily_file.exists():
        with open(daily_file, "r") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    key = rec.get("key")
                    if key and key not in records_by_key:
                        records_by_key[key] = rec

    with open(daily_file, "w") as f:
        for key in sorted(records_by_key.keys()):
            f.write(json.dumps(records_by_key[key]) + "\n")

    total_lines = len(records_by_key)
    print(f"Total records: {total_lines}")

    today = datetime.now(timezone.utc).date()
    last_14_days = {(today - timedelta(days=i)).isoformat() for i in range(14)}

    clones_uniques = 0
    views_uniques = 0
    referrers_by_count = defaultdict(int)

    for rec in records_by_key.values():
        if rec["date"] in last_14_days:
            if rec["metric"] == "clones":
                clones_uniques += rec["uniques"]
            elif rec["metric"] == "views":
                views_uniques += rec["uniques"]
            elif rec["metric"] == "referrers":
                referrers_by_count[rec["label"]] += rec["uniques"]

    print(f"Clones (14d): {clones_uniques} unique")
    print(f"Views (14d): {views_uniques} unique")

    print("Top 5 referrers (14d):")
    top_referrers = sorted(referrers_by_count.items(), key=lambda x: x[1], reverse=True)[:5]
    if top_referrers:
        for ref, count in top_referrers:
            print(f"  {ref}: {count}")
    else:
        print("  (none)")

    print("SNAPSHOT_OK")


if __name__ == "__main__":
    main()
