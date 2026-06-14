from __future__ import annotations

from burnless import cli
from burnless.maestro.engine import MaestroState, RollingCapsule, Turn, force_compact


def test_chat_rollover_flag_parses_after_subcommand():
    parser = cli.build_parser()
    args = parser.parse_args(["chat", "--rollover-turns", "3"])
    assert args.rollover_turns == 3
    assert args.cmd == "chat"


def test_force_compact_prepares_next_seed_and_writes_capsule(tmp_path):
    state = MaestroState(
        rolling_capsule=RollingCapsule(summary="old"),
        window=[
            Turn("user", "keep-me", 2),
            Turn("maestro", "keep-me-too", 2),
            Turn("user", "compact-me", 2),
            Turn("maestro", "compact-response", 2),
        ],
    )
    seen: list[str] = []

    def compact_fn(blob: str) -> dict:
        seen.append(blob)
        return {
            "decisions": ["D1"],
            "constraints": ["C1"],
            "open_threads": ["open"],
            "summary": "capsule-summary",
        }

    assert force_compact(state, compact_fn, burnless_root=tmp_path, keep_tail_turns=1) is True
    assert state.cycle == 1
    assert len(state.window) == 1
    assert state.window[0].text == "compact-response"
    assert "capsule-summary" in state.pending_seed
    assert "## Recent" in state.pending_seed
    assert "keep-me-too" not in state.pending_seed
    assert seen and "user: keep-me" in seen[0]

    capsule = tmp_path / "maestro" / "rolling" / "capsule_1.json"
    assert capsule.exists()
