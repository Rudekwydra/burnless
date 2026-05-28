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
    assert "--system-prompt" in cmd
    assert "--append-system-prompt" not in cmd
    assert "--tools" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--disallowedTools" not in cmd
    assert "--output-format" in cmd
    assert "json" in cmd
    assert '{"intent":"x"}' in cmd


def test_build_command_disallows_execution_tools():
    cmd = mr.build_command('{"intent":"x"}')
    assert "--tools" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""


def test_system_prompt_has_hard_rules():
    p = mr.MAESTRO_SYSTEM_PROMPT
    assert "do NOT" in p or "do NOT perform" in p
    assert "ONE line" in p
    assert "NEVER" in p or "Never" in p


def test_extract_telegram_ignores_prose_preamble():
    raw = 'Let me think.\nReasoning here.\n{"to":"gold","need":"plan","of":"x"}'
    assert mr.extract_telegram(raw) == '{"to":"gold","need":"plan","of":"x"}'


def test_extract_telegram_picks_last_valid():
    raw = '{"draft":"ignore"} more thought {"to":"silver","run":"y"}'
    assert mr.extract_telegram(raw) == '{"to":"silver","run":"y"}'


def test_extract_telegram_clean_passthrough():
    assert mr.extract_telegram('{"done":"z"}') == '{"done":"z"}'


def test_extract_telegram_fence_fallback():
    out = mr.extract_telegram('```\nnot json here\n```')
    assert "```" not in out


def test_system_prompt_has_approval_gate():
    p = mr.MAESTRO_SYSTEM_PROMPT
    assert "ask_user" in p
    assert "authorization" in p.lower()
    assert "production" in p.lower()
