from unittest.mock import patch, MagicMock
from burnless.codec import ollama_bronze


def test_is_available_false_when_no_binary():
    with patch("shutil.which", return_value=None):
        assert ollama_bronze.is_available() is False


def test_encode_falls_back_when_unavailable():
    with patch.object(ollama_bronze, "is_available", return_value=False):
        result = ollama_bronze.encode("some long text here")
        assert result.used_ollama is False
        assert result.compressed_text == "some long text here"
        assert result.ratio == 1.0


def test_encode_returns_compressed_when_ollama_compresses():
    mock_proc = MagicMock(returncode=0, stdout="short tele text")
    with patch.object(ollama_bronze, "is_available", return_value=True), \
         patch("subprocess.run", return_value=mock_proc):
        result = ollama_bronze.encode("a very long original text " * 5)
        assert result.used_ollama is True
        assert result.ratio > 1.05
        assert result.compressed_text == "short tele text"


def test_encode_falls_back_on_subprocess_error():
    with patch.object(ollama_bronze, "is_available", return_value=True), \
         patch("subprocess.run", side_effect=OSError("boom")):
        result = ollama_bronze.encode("text")
        assert result.used_ollama is False
        assert result.ratio == 1.0


def test_encode_falls_back_when_ratio_too_low():
    # Compression didn't help — same length → ratio == 1.0 < 1.05 threshold → fallback
    mock_proc = MagicMock(returncode=0, stdout="text content there")  # same length as input
    with patch.object(ollama_bronze, "is_available", return_value=True), \
         patch("subprocess.run", return_value=mock_proc):
        result = ollama_bronze.encode("text content there")
        assert result.used_ollama is False
        assert result.ratio == 1.0
