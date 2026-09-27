import subprocess

import pytest

from vibelove import repair_guard as guard


def verdict(text, status='FAIL'):
    return {'status': status, 'criteria': text}


def test_checkpoint_restores_modified_deleted_and_added_files(tmp_path):
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    (tmp_path / 'app.py').write_text('x = 1\n')
    (tmp_path / 'keep.txt').write_text('keep')
    saved = guard.checkpoint(str(tmp_path))
    (tmp_path / 'app.py').write_text('broken(')
    (tmp_path / 'keep.txt').unlink()
    (tmp_path / 'new.py').write_text('bad')
    guard.restore(str(tmp_path), saved)
    assert (tmp_path / 'app.py').read_text() == 'x = 1\n'
    assert (tmp_path / 'keep.txt').read_text() == 'keep'
    assert not (tmp_path / 'new.py').exists()
    assert not guard.git(str(tmp_path), 'status', '--porcelain')
    assert len(guard.git(str(tmp_path), 'log', '--oneline').splitlines()) == 3


def test_no_repository_no_repair(tmp_path):
    with pytest.raises(RuntimeError):
        guard.checkpoint(str(tmp_path))


def test_repeated_checks_detect_regression_even_with_qa_pass(tmp_path):
    path = tmp_path / 'app.py'
    path.write_text('x = 1\n')
    plan = guard.discover_checks(str(tmp_path))
    before = guard.run_checks(str(tmp_path), plan)
    path.write_text('x = (')
    after = guard.run_checks(str(tmp_path), plan)
    assert not guard.improvement(verdict('1. [FAIL] missing'),
                                 verdict('1. [PASS] fixed', 'PASS'), before, after)[0]


@pytest.mark.parametrize('current', [
    verdict('1. [PASS] ok\n2. [FAIL] still broken'),
    verdict('1. [FAIL] regressed\n2. [PASS] fixed'),
    verdict('1. [PASS] ok\n2. [UNVERIFIED] unknown', 'PASS'),
    {'status': 'error'},
    verdict('1. [PASS] incomplete', 'PASS'),
])
def test_no_progress_or_regression_rejected(current):
    assert not guard.improvement(verdict('1. [PASS] ok\n2. [FAIL] broken'), current, {}, {})[0]


def test_real_progress_accepted():
    assert guard.improvement(verdict('1. [PASS] ok\n2. [FAIL] broken'),
                             verdict('1. [PASS] ok\n2. [PASS] fixed', 'PASS'), {}, {})[0]
