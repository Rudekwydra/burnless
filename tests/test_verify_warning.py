from burnless.spec_validator import verify_block_is_silent_noop


def test_section_present_no_fenced_block():
    text = "## Verify\nrun pytest\ncheck output"
    assert verify_block_is_silent_noop(text) is True


def test_section_present_with_fenced_block():
    text = "## Verify\n```sh\npytest tests/\n```"
    assert verify_block_is_silent_noop(text) is False


def test_no_verify_section():
    text = "## Goal\ndo something\n## Task\nfix bug"
    assert verify_block_is_silent_noop(text) is False
