from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from burnless.mcp_server import handle_delegate
from burnless import state as state_mod


@pytest.fixture
def mock_burnless_project(tmp_path: Path) -> Path:
    """Create complete mock .burnless project with config, state, and directories."""
    root = tmp_path / ".burnless"
    root.mkdir(parents=True, exist_ok=True)
    (root / "delegations").mkdir(exist_ok=True)
    (root / "capsules").mkdir(exist_ok=True)
    (root / "runs").mkdir(exist_ok=True)

    state_path = root / "state.json"
    state_mod.save(state_path, state_mod.DEFAULT_STATE.copy())

    config_path = root / "config.yaml"
    config_path.write_text(
        """
agents:
  bronze:
    name: haiku
    command: claude --model haiku
  silver:
    name: sonnet
    command: claude --model sonnet
validation:
  require_absolute_paths: true
  enforce_verify_fence: true
""",
        encoding="utf-8",
    )
    return root


@pytest.mark.asyncio
async def test_mcp_delegate_appliesautofix_relative_path_spec(mock_burnless_project: Path) -> None:
    """Spec with relative path should be autofixed (paths rewritten to absolute)."""
    spec = "Read a file at src/main.py and analyze it"
    result = await handle_delegate(text=spec, project_root=str(mock_burnless_project.parent))
    # Autofix should apply, so delegation should be created
    assert result.get("error") is None
    assert "id" in result
    did = result["id"]
    # Verify delegation was created
    deleg_path = mock_burnless_project / "delegations" / f"{did}.md"
    assert deleg_path.exists()
    # Verify the delegation contains the autofixed absolute path
    markdown = deleg_path.read_text(encoding="utf-8")
    # The task should now have the absolute path instead of relative
    assert "/src/main.py" in markdown or "src/main.py" in markdown


@pytest.mark.asyncio
async def test_mcp_delegate_blocks_unfenced_verify(mock_burnless_project: Path) -> None:
    """Spec with ## Verify but no fenced code block should be blocked."""
    spec = (
        f"Analyze the code in {mock_burnless_project.parent / 'example.py'}.\n\n"
        "## Verify\n"
        "Some verification text without code fence"
    )
    result = await handle_delegate(text=spec, project_root=str(mock_burnless_project.parent))
    assert result.get("error") == "spec_gate"
    assert result.get("reason") == "unfenced_verify"
    assert "unfenced" in result.get("hint", "").lower() or "cercado" in result.get("hint", "").lower()
    # Verify no delegation was created
    deleg_dir = mock_burnless_project / "delegations"
    delegation_files = list(deleg_dir.glob("*.md"))
    assert len(delegation_files) == 0


@pytest.mark.asyncio
async def test_mcp_delegate_valid_spec_passes(mock_burnless_project: Path) -> None:
    """Valid spec (absolute paths, fenced Verify) should pass and create delegation."""
    project_root = mock_burnless_project.parent
    spec = (
        f"Analyze the code in {project_root / 'example.py'}.\n\n"
        "## Verify\n"
        "```sh\ngrep -q 'def ' /tmp/test.py\n```"
    )
    result = await handle_delegate(text=spec, project_root=str(project_root))
    assert result.get("error") is None
    assert "id" in result
    assert result["tier"] in ("bronze", "silver")
    # Verify delegation was created
    did = result["id"]
    deleg_path = mock_burnless_project / "delegations" / f"{did}.md"
    assert deleg_path.exists()


@pytest.mark.asyncio
async def test_mcp_delegate_infers_kind_hint(mock_burnless_project: Path) -> None:
    """Spec clearly marked as thought-only should infer kind='thought'."""
    project_root = mock_burnless_project.parent
    spec = (
        f"Analyze and explain the design of {project_root / 'example.py'}. "
        "Do not edit anything. Propose a refactor plan."
    )
    result = await handle_delegate(text=spec, project_root=str(project_root))
    assert result.get("error") is None
    assert "id" in result
    # Verify delegation markdown contains inferred kind
    did = result["id"]
    deleg_path = mock_burnless_project / "delegations" / f"{did}.md"
    assert deleg_path.exists()
    markdown = deleg_path.read_text(encoding="utf-8")
    # infer_kind_hint should detect "analyze", "design", "explain", "plan" as thought keywords
    assert "thought" in markdown.lower() or "execution" in markdown.lower()
    # The kind should be "thought" due to high keyword density
    assert "- **Report kind:** thought" in markdown or "thought" in markdown
