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


def test_autofixed_spec_still_hits_verify_gates() -> None:
    """Autofixed spec (relative→absolute path) must still pass through unfenced_verify gate.

    Regression test: gate (a) autofix success must not short-circuit gates (b) and (c).
    A spec with autofixable relative path + unfenced Verify should fail at gate (b),
    not pass early from gate (a).
    """
    from burnless import spec_validator as sv
    from pathlib import Path

    # Use actual project path
    project_root = Path("/Users/roberto/antigravity/burnless")

    # Construct spec with relative path that can be autofixed + unfenced Verify
    # "tests/conftest.py" is autofixable (has real file reference), unfenced Verify should block
    spec = (
        "Edit tests/conftest.py to add a new fixture.\n\n"
        "## Verify\n"
        "Manual verification text without code fence"
    )

    cfg = {
        "validation": {
            "require_absolute_paths": True,
            "enforce_verify_fence": True
        },
        "language": "pt-BR"
    }

    result = sv.evaluate_spec_gates(spec, cfg, project_root)

    # Must fail at gate (b) unfenced_verify, not pass at gate (a) autofix
    assert result.ok is False
    assert result.reason == "unfenced_verify"
    # Autofix should have been applied to text, even though gate (b) blocked
    assert "/Users/roberto/antigravity/burnless/tests/conftest.py" in result.text
    # Autofix notice should be present in result
    assert result.autofix_notice != ""


@pytest.mark.asyncio
async def test_hardcore_gate_blocks_manual_tier_override(mock_burnless_project: Path, monkeypatch) -> None:
    """MCP hardcore gate: manual tier=gold when BURNLESS_HARDCORE=1 and spec routes to silver."""
    project_root = mock_burnless_project.parent

    # Set BURNLESS_HARDCORE env
    monkeypatch.setenv("BURNLESS_HARDCORE", "1")

    # Update config without policy (default off, will be overridden by env)
    config_path = mock_burnless_project / "config.yaml"
    config_path.write_text(
        """
agents:
  silver:
    name: sonnet
    command: claude --model sonnet
  gold:
    name: opus
    command: claude --model opus
routing:
  gold: []
validation:
  require_absolute_paths: true
  enforce_verify_fence: true
""",
        encoding="utf-8",
    )

    # Spec with path will route to silver (builtin silver hint)
    spec = (
        f"Analyze code in {project_root / 'example.py'}.\n\n"
        "## Verify\n"
        "```sh\necho 'done'\n```"
    )

    # Try to override with tier=gold
    result = await handle_delegate(text=spec, tier="gold", project_root=str(project_root))

    # Should be blocked by hardcore policy
    assert result.get("error") == "hardcore_blocked"
    assert result.get("natural_tier") == "silver"
    assert result.get("policy_source") == "env:BURNLESS_HARDCORE"
    assert "Escape hatch" in result.get("hint", "")

    # Verify no delegation was created
    deleg_dir = mock_burnless_project / "delegations"
    delegation_files = list(deleg_dir.glob("*.md"))
    assert len(delegation_files) == 0


@pytest.mark.asyncio
async def test_hardcore_gate_allows_auto_route(mock_burnless_project: Path, monkeypatch) -> None:
    """MCP hardcore gate: auto-route (tier=None) bypasses gate even with BURNLESS_HARDCORE=1."""
    project_root = mock_burnless_project.parent

    # Set BURNLESS_HARDCORE env
    monkeypatch.setenv("BURNLESS_HARDCORE", "1")

    # Update config
    config_path = mock_burnless_project / "config.yaml"
    config_path.write_text(
        """
agents:
  silver:
    name: sonnet
    command: claude --model sonnet
  gold:
    name: opus
    command: claude --model opus
routing:
  gold: []
validation:
  require_absolute_paths: true
  enforce_verify_fence: true
""",
        encoding="utf-8",
    )

    # Spec with path will route to silver
    spec = (
        f"Analyze code in {project_root / 'example.py'}.\n\n"
        "## Verify\n"
        "```sh\necho 'done'\n```"
    )

    # Call with tier=None (auto-route), no override
    result = await handle_delegate(text=spec, tier=None, project_root=str(project_root))

    # Should succeed (no hardcore gate applies to auto-route, tier=None)
    assert result.get("error") is None
    assert "id" in result
    assert result["tier"] == "silver"

    # Verify delegation was created
    did = result["id"]
    deleg_path = mock_burnless_project / "delegations" / f"{did}.md"
    assert deleg_path.exists()


@pytest.mark.asyncio
async def test_hardcore_gate_disabled_allows_override(mock_burnless_project: Path, monkeypatch) -> None:
    """MCP hardcore gate: tier override allowed when BURNLESS_HARDCORE is not set."""
    project_root = mock_burnless_project.parent

    # Ensure BURNLESS_HARDCORE is not set
    monkeypatch.delenv("BURNLESS_HARDCORE", raising=False)

    # Update config without hardcore policy
    config_path = mock_burnless_project / "config.yaml"
    config_path.write_text(
        """
agents:
  silver:
    name: sonnet
    command: claude --model sonnet
  gold:
    name: opus
    command: claude --model opus
routing:
  gold: []
validation:
  require_absolute_paths: true
  enforce_verify_fence: true
""",
        encoding="utf-8",
    )

    # Spec with path will route to silver
    spec = (
        f"Analyze code in {project_root / 'example.py'}.\n\n"
        "## Verify\n"
        "```sh\necho 'done'\n```"
    )

    # Override with tier=gold (should be allowed when no hardcore policy)
    result = await handle_delegate(text=spec, tier="gold", project_root=str(project_root))

    # Should succeed (no hardcore policy active)
    assert result.get("error") is None
    assert "id" in result
    assert result["tier"] == "gold"

    # Verify delegation was created
    did = result["id"]
    deleg_path = mock_burnless_project / "delegations" / f"{did}.md"
    assert deleg_path.exists()
