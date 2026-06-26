def summarize(records):
    """
    Aggregates audit-graph records into summary statistics.
    Defensive implementation against missing or malformed input.
    """
    total = 0
    by_status = {}
    verify_passed = 0
    verify_failed = 0
    files_total = 0

    for record in records:
        if not isinstance(record, dict):
            continue # Skip non-dict entries defensively
        
        total += 1 # Count valid dictionary entry as a record if it's an input item

        # Status tracking (handle None -> "?")
        status = record.get("status")
        status_key = status if status is not None else "?"
        by_status[status_key] = by_status.get(status_key, 0) + 1

        # Verification tracking
        verify_status = record.get("verify_status")
        if verify_status == "passed":
            verify_passed += 1
        elif verify_status == "failed":
            verify_failed += 1

        # Files declared tracking (defensive)
        files_declared = record.get("files_declared")
        file_count = 0
        if isinstance(files_declared, list):
            file_count = len(files_declared)

        files_total += file_count

    return {
        "total": total,
        "by_status": by_status,
        "verify_passed": verify_passed,
        "verify_failed": verify_failed,
        "files_total": files_total,
    }

def render_summary(stats):
    """
    Renders the summary statistics dict into a human-readable line.
    """
    total = stats.get("total", 0)
    by_status = stats.get("by_status", {})
    verify_passed = stats.get("verify_passed", 0)
    verify_failed = stats.get("verify_failed", 0)
    files_total = stats.get("files_total", 0)

    # Format status pairs
    if by_status:
        # Render as "OK 2 / PART 1" (iteration order preserved in Python 3.7+)
        status_pairs = " / ".join(f"{key} {count}" for key, count in by_status.items())
    else:
        status_pairs = "—"

    return (f"audit: {total} records · {status_pairs} · verify {verify_passed}✓ {verify_failed}✗ · {files_total} files")