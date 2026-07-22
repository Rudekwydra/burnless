import json
import threading
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from burnless import chat, chat_serve


CHAIN_ID = "chain-live"
SESSION_1 = "session-alpha"
SESSION_2 = "session-beta"


def _turn(role: str, text: str, ts: str) -> str:
    return json.dumps({"timestamp": ts, "message": {"role": role, "content": text}})


@pytest.fixture
def chat_artifacts(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    project_root = tmp_path / "project"
    burnless_root = project_root / ".burnless"
    chain_dir = burnless_root / "epochs" / "_rolling" / "chains" / CHAIN_ID
    chain_dir.mkdir(parents=True)
    (chain_dir / "chain.json").write_text(
        json.dumps({"chain_id": CHAIN_ID, "host": "claude"}),
        encoding="utf-8",
    )

    sessions_root = burnless_root / "epochs" / "sessions" / "claude"
    for session_id, updated_at in (
        (SESSION_1, "2026-07-21T10:01:00Z"),
        (SESSION_2, "2026-07-21T10:03:00Z"),
    ):
        session_dir = sessions_root / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "checkpoint.json").write_text(
            json.dumps(
                {
                    "chain_id": CHAIN_ID,
                    "host_session_id": session_id,
                    "updated_at": updated_at,
                }
            ),
            encoding="utf-8",
        )

    projects_root = tmp_path / "claude-projects"
    transcript_dir = projects_root / "synthetic-project"
    transcript_dir.mkdir(parents=True)
    (transcript_dir / f"{SESSION_1}.jsonl").write_text(
        "\n".join(
            [
                _turn("user", "primeira pergunta", "2026-07-21T10:00:00Z"),
                _turn("assistant", "primeira resposta", "2026-07-21T10:00:30Z"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (transcript_dir / f"{SESSION_2}.jsonl").write_text(
        "\n".join(
            [
                _turn("user", "segunda pergunta", "2026-07-21T10:03:00Z"),
                _turn("assistant", "segunda resposta", "2026-07-21T10:03:30Z"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    original_find = chat.find_transcript
    monkeypatch.setattr(
        chat,
        "find_transcript",
        lambda session_id, projects_root=None, **kwargs: original_find(
            session_id, projects_root=projects_root or transcript_dir.parent
        ),
    )
    return {"tmp_path": tmp_path, "project_root": project_root}


@pytest.fixture
def live_server(chat_artifacts):
    try:
        server = chat_serve.create_chat_server(chat_artifacts["project_root"], CHAIN_ID, 0)
    except PermissionError:
        # Some CI sandboxes deny bind(2), including on loopback. Exercise the
        # exact same BaseHTTPRequestHandler directly in that environment.
        class DirectServer:
            server_address = (chat_serve.HOST, 0)
            handler = chat_serve._handler_for(chat_artifacts["project_root"], CHAIN_ID)

        yield DirectServer(), None
        return

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield server, f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _get(live_server, path: str) -> tuple[int, str, bytes]:
    server, base_url = live_server
    if base_url is not None:
        try:
            with urlopen(f"{base_url}{path}") as response:
                return response.status, response.headers.get_content_type(), response.read()
        except HTTPError as error:
            try:
                return error.code, error.headers.get_content_type(), error.read()
            finally:
                error.close()

    handler = object.__new__(server.handler)
    handler.path = path
    handler.wfile = BytesIO()
    captured: dict[str, object] = {"headers": {}}
    handler.send_response = lambda status: captured.update(status=status)
    handler.send_header = lambda name, value: captured["headers"].__setitem__(name, value)
    handler.end_headers = lambda: None
    handler.do_GET()
    content_type = str(captured["headers"]["Content-Type"]).split(";", 1)[0]
    return int(captured["status"]), content_type, handler.wfile.getvalue()


def _snapshot_files(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_timeline_json_contract_and_stitched_order(live_server):
    status, content_type, body = _get(live_server, "/timeline.json")
    assert status == 200
    assert content_type == "application/json"
    events = json.loads(body)

    required = {"chain_id", "session_id", "seq", "role", "ts", "text", "kind"}
    assert isinstance(events, list)
    assert all(required <= set(event) for event in events)
    turns = [event for event in events if event["kind"] == "turn"]
    assert [event["text"] for event in turns] == [
        "primeira pergunta",
        "primeira resposta",
        "segunda pergunta",
        "segunda resposta",
    ]
    assert [event["session_id"] for event in turns] == [
        SESSION_1,
        SESSION_1,
        SESSION_2,
        SESSION_2,
    ]


def test_timeline_has_exactly_one_session_boundary(live_server):
    _, _, body = _get(live_server, "/timeline.json")
    events = json.loads(body)

    boundaries = [event for event in events if event["kind"] == "boundary"]
    assert len(boundaries) == 1
    assert boundaries[0]["session_id"] == SESSION_2


def test_root_serves_self_contained_polling_page(live_server):
    status, content_type, payload = _get(live_server, "/")
    body = payload.decode("utf-8")
    assert status == 200
    assert content_type == "text/html"

    assert 'id="timeline"' in body
    assert 'fetch("/timeline.json")' in body


def test_unknown_route_is_404(live_server):
    status, content_type, body = _get(live_server, "/unknown")
    assert status == 404
    assert content_type == "text/plain"
    assert body == b"not found\n"


def test_server_binds_loopback_only(live_server):
    server, _ = live_server
    assert server.server_address[0] == "127.0.0.1"


def test_requests_do_not_create_or_modify_fixture_files(chat_artifacts, live_server):
    before = _snapshot_files(chat_artifacts["tmp_path"])

    _get(live_server, "/")
    _get(live_server, "/timeline.json")

    assert _snapshot_files(chat_artifacts["tmp_path"]) == before
