from burnless.spec_validator import should_block_unfenced_verify

FENCED = "## Verify\n```sh\npytest -q\n```\n"
UNFENCED = "## Verify\nO teste DEVE rodar pytest e passar.\npython3 -m pytest -q\n"
NO_VERIFY = "Faca a tarefa X.\n"


def test_unfenced_blocks_when_enforced():
    assert should_block_unfenced_verify(UNFENCED, enforce=True, allow_override=False) is True


def test_unfenced_allowed_with_override():
    assert should_block_unfenced_verify(UNFENCED, enforce=True, allow_override=True) is False


def test_unfenced_allowed_when_enforcement_off():
    assert should_block_unfenced_verify(UNFENCED, enforce=False, allow_override=False) is False


def test_fenced_never_blocks():
    assert should_block_unfenced_verify(FENCED, enforce=True, allow_override=False) is False


def test_no_verify_section_never_blocks():
    assert should_block_unfenced_verify(NO_VERIFY, enforce=True, allow_override=False) is False


def test_validation_heading_flagged_as_deprecated():
    from burnless import spec_validator as s
    f = "`" * 3
    md_a = "## Validation\n" + f + "sh\necho x\n" + f
    md_v = "## Verify\n" + f + "sh\necho x\n" + f
    assert s.uses_deprecated_validation_heading(md_a) is True
    assert s.uses_deprecated_validation_heading(md_v) is False
    w = s.format_validation_alias_warning("en")
    assert "Validation" in w and "Verify" in w
