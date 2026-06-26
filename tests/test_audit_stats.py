from burnless.audit_stats import summarize, render_summary

def test_summarize_counts():
    # Example from prompt
    records = [
        {"status":"OK","verify_status":"passed","files_declared":["/a.py"]},
        {"status":"OK","verify_status":"failed"},
        {"status":"PART","files_declared":["/x","/y"]},
    ]
    expected = {
        "total": 3,
        "by_status": {"OK": 2, "PART": 1},
        "verify_passed": 1,
        "verify_failed": 1,
        "files_total": 3,
    }
    assert summarize(records) == expected

def test_summarize_empty():
    # Empty list handling
    expected = {"total":0,"by_status":{},"verify_passed":0,"verify_failed":0,"files_total":0}
    assert summarize([]) == expected

def test_summarize_defensive():
    # Defensive checks: missing keys, non-list files_declared
    records = [{}, {"files_declared":"notalist"}]
    stats = summarize(records)
    
    # Total 2 records
    assert stats["total"] == 2
    
    # All entries map to "?" status because "status" is missing or record is malformed for that key
    # Empty dict {} -> status=None (treated as ?)
    # Record {"files_declared":"notalist"} -> status=None (treated as ?)
    assert stats["by_status"] == {"?": 2}
    
    # files_total must be 0 because "notalist" is not a list.
    assert stats["files_total"] == 0
    # verify_status missing -> defaults are 0
    assert stats["verify_passed"] == 0
    assert stats["verify_failed"] == 0

def test_render_summary():
    # Test the specific example rendering
    stats = {
        "total":3, "by_status":{"OK":2,"PART":1}, 
        "verify_passed":1, "verify_failed":1, "files_total":3
    }
    expected_string = "audit: 3 records · OK 2 / PART 1 · verify 1✓ 1✗ · 3 files"
    assert render_summary(stats) == expected_string

def test_render_summary_empty():
    # Test rendering when summarize([]) is passed
    empty_stats = {"total":0,"by_status":{},"verify_passed":0,"verify_failed":0,"files_total":0}
    result = render_summary(empty_stats)
    assert "0 records" in result
    # Status pairs should render as "—" when by_status is empty
    assert "—" in result