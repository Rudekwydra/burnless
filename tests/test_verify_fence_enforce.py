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
