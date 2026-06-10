from burnless.delegations import extract_result_json


def test_inner_fence_ignored():
    stdout = (
        "Here is an example:\n"
        "```json\n"
        "{\"x\": 1, \"example\": true}\n"
        "```\n"
        "And now the real envelope:\n"
        "```json\n"
        "{\"status\": \"OK\", \"summary\": \"real\", \"files_touched\": [], "
        "\"validated\": [], \"evidence\": [], \"issues\": [], \"next\": \"\"}\n"
        "```"
    )
    result = extract_result_json(stdout)
    assert result is not None, "expected a result"
    assert result.get("status") == "OK", f"expected OK, got: {result}"


def test_trailing_bare_json_still_works():
    stdout = 'Some output\n{"status":"OK","summary":"bare","files_touched":[],"validated":[],"evidence":[],"issues":[],"next":""}'
    result = extract_result_json(stdout)
    assert result is not None
    assert result.get("status") == "OK"


def test_channel_tokens_stripped():
    stdout = (
        "<|channel>some preamble<channel|>\n"
        "```json\n"
        "{\"status\": \"OK\", \"summary\": \"channel test\", \"files_touched\": [], "
        "\"validated\": [], \"evidence\": [], \"issues\": [], \"next\": \"\"}\n"
        "```"
    )
    result = extract_result_json(stdout)
    assert result is not None
    assert result.get("status") == "OK"
