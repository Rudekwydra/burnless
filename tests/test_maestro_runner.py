from burnless import maestro_runner as mr


def test_strip_fences_removes_json_fence():
    assert mr.strip_fences('```json\n{"to":"gold"}\n```') == '{"to":"gold"}'


def test_strip_fences_removes_plain_fence():
    assert mr.strip_fences('```\n{"done":"x"}\n```') == '{"done":"x"}'


def test_strip_fences_noop_on_clean():
    assert mr.strip_fences('{"to":"silver","run":"x"}') == '{"to":"silver","run":"x"}'


def test_build_command_has_isolation_flags():
    cmd = mr.build_command('{"intent":"x"}', "claude-haiku-4-5-20251001")
    joined = " ".join(cmd)
    assert "--setting-sources" in cmd
    assert "project,local" in cmd
    assert "--exclude-dynamic-system-prompt-sections" in cmd
    assert "--disallowedTools" in cmd
    assert "--output-format" in cmd
    assert "json" in cmd
    assert '{"intent":"x"}' in cmd


def test_build_command_disallows_execution_tools():
    cmd = mr.build_command('{"intent":"x"}')
    idx = cmd.index("--disallowedTools")
    tools = cmd[idx + 1]
    for t in ("Read", "Edit", "Write", "Bash"):
        assert t in tools


def test_system_prompt_has_hard_rules():
    p = mr.MAESTRO_SYSTEM_PROMPT
    assert "do NOT" in p or "do NOT perform" in p
    assert "ONE line" in p
    assert "NEVER" in p or "Never" in p
