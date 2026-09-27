import os
import subprocess
import sys

import pytest

from test_mc import mc, _clean_state


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(mc, "PROJECT_ROOT", str(root.resolve()))
    return root


@pytest.mark.parametrize("path", ["../secret", "../../secret", "/etc/passwd"])
def test_external_file_paths_rejected(project, path):
    assert not mc.do_read_file({"path": path})[0]
    assert not mc.do_write_file({"path": path, "content": "changed"})[0]
    assert not mc.do_list_dir({"path": path})[0]


def test_external_symlinks_and_hardlinks(project):
    secret = project.parent / "secret"
    secret.write_text("private")
    (project / "link").symlink_to(secret)
    (project / "outside").symlink_to(project.parent, target_is_directory=True)
    os.link(secret, project / "hardlink")
    for path in ("link", "hardlink", "outside/secret"):
        assert not mc.do_read_file({"path": path})[0]
        assert not mc.do_write_file({"path": path, "content": "changed"})[0]
    assert secret.read_text() == "private"
    assert mc._project_path_error("outside/new.txt")
    files = [f for _, _, names in mc._project_walk(".") for f in names]
    assert "link" not in files


def test_internal_symlink_and_fixed_root(project, monkeypatch):
    (project / "local").write_text("hello")
    (project / "link").symlink_to(project / "local")
    assert mc.do_read_file({"path": "link"})[0]
    monkeypatch.chdir(project.parent)
    assert mc._project_path_error("secret")
    assert not mc._project_path_error(project / "local")


def test_batch_preflight_prevents_partial_write(project):
    ok, _ = mc.do_write_files({"files": [
        {"path": "ok.txt", "content": "ok"},
        {"path": "../bad.txt", "content": "bad"},
    ]})
    assert not ok
    assert not (project / "ok.txt").exists()


@pytest.mark.parametrize("command", [
    "cd ../", "cat /etc/passwd", "cat ../secret", "echo hi > ../file",
    "defaults write com.apple.dock foo bar", "killall ControlCenter",
    "npm install -g something", "pip install something", "kill 1",
])
def test_shell_visible_hazards(project, command):
    assert mc._shell_guard(command)


@pytest.mark.parametrize("command", ["npm run build", "python3 -m venv .venv", "ls -la", "echo hello > local.txt"])
def test_normal_commands_allowed(project, command):
    assert not mc._shell_guard(command)


def test_venv_python_symlink_execution_only(project):
    bindir = project / ".venv" / "bin"
    bindir.mkdir(parents=True)
    (bindir.parent / "pyvenv.cfg").write_text("home = test")
    (bindir / "python").symlink_to(sys.executable)
    assert not mc._shell_guard(".venv/bin/python -m pip install flask")
    assert mc._project_path_error(".venv/bin/python")


def test_environment_and_run(project, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.setenv("MC_SECRET", "private")
    env = mc._run_environment()
    assert "OPENROUTER_API_KEY" not in env
    assert "MC_SECRET" not in env
    for key in ("HOME", "TMPDIR", "XDG_CACHE_HOME"):
        assert not mc._project_path_error(env[key])
    monkeypatch.setattr(mc, "AUTO_YES", True)
    assert mc.do_run({"command": "echo hello > local.txt"})[0]
    assert (project / "local.txt").read_text().strip() == "hello"
    assert not mc.do_run({"command": "exit 2"})[0]


def test_git_does_not_discover_parent_repository(project):
    subprocess.run(["git", "init", str(project.parent)], capture_output=True, check=True)
    assert mc._git("rev-parse", "--is-inside-work-tree")[0] != 0


def test_external_notes_and_transcript(project):
    target = project.parent / "notes"
    target.write_text("private")
    (project / "notes").symlink_to(target)
    with pytest.raises(PermissionError):
        mc._project_open("notes", "w")
    assert target.read_text() == "private"
