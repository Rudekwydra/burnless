import os
import subprocess
import sys
import pytest
from burnless.recovery import _pid_is_dead_or_reused, _process_name_best_effort


def test_reused_pid_treated_as_dead():
    """pid = str(os.getpid()) (vivo), expected_proc_name = proc-que-nao-existe-xyz => True."""
    pid = str(os.getpid())
    expected_proc_name = "proc-que-nao-existe-xyz"
    assert _pid_is_dead_or_reused(pid, expected_proc_name) is True


def test_same_proc_name_alive():
    """pid = str(os.getpid()), expected = _process_name_best_effort(str(os.getpid())) (não-vazio) => False."""
    pid = str(os.getpid())
    expected = _process_name_best_effort(pid)
    assert expected != "", "Process name should not be empty for current process"
    assert _pid_is_dead_or_reused(pid, expected) is False


def test_empty_expected_name_backcompat():
    """pid = str(os.getpid()), expected = "" => False (backcompat, assume vivo)."""
    pid = str(os.getpid())
    assert _pid_is_dead_or_reused(pid, "") is False


def test_dead_pid_still_dead():
    """subprocess sys.executable -c pass com wait() concluído; pid dele, expected qualquer => True."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc_pid = str(proc.pid)
    proc.wait()
    assert _pid_is_dead_or_reused(proc_pid, "any-proc-name") is True


def test_host_prefixed_pid():
    """pid = f"host-{os.getpid()}", expected = "proc-que-nao-existe-xyz" => True (prova _extract_os_pid)."""
    pid = f"host-{os.getpid()}"
    expected_proc_name = "proc-que-nao-existe-xyz"
    assert _pid_is_dead_or_reused(pid, expected_proc_name) is True
