from __future__ import annotations

import json

from burnless.agents import _parse_worker_usage


def test_parse_worker_usage_stream_json():
    sj = '{"type":"system"}\n' + json.dumps(
        {"type": "result", "usage": {"cache_read_input_tokens": 12345, "output_tokens": 50}}
    )
    result = _parse_worker_usage(sj)
    assert result.get("cache_read_input_tokens") == 12345
    assert result.get("output_tokens") == 50


def test_parse_worker_usage_plain_json():
    pj = json.dumps({"result": "x", "usage": {"cache_read_input_tokens": 777}})
    result = _parse_worker_usage(pj)
    assert result.get("cache_read_input_tokens") == 777


def test_parse_worker_usage_garbage():
    assert _parse_worker_usage("garbage") == {}


def test_parse_worker_usage_empty():
    assert _parse_worker_usage("") == {}


def test_parse_worker_usage_picks_last_result_event():
    line1 = json.dumps({"type": "result", "usage": {"cache_read_input_tokens": 100}})
    line2 = json.dumps({"type": "result", "usage": {"cache_read_input_tokens": 200}})
    result = _parse_worker_usage(line1 + "\n" + line2)
    assert result.get("cache_read_input_tokens") == 200
