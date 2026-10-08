import json

from platespinner import install

HOOK = "/opt/ps/bin/platespinner-hook"


def write(path, data):
    path.write_text(json.dumps(data))


def test_install_merges_and_keeps_existing_hooks(tmp_path):
    settings = tmp_path / "settings.json"
    write(settings, {"model": "x", "hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": "/home/u/.claude/hooks/other-hook.sh"}]}]}})
    install.install_hooks(settings, HOOK)
    data = json.loads(settings.read_text())
    assert data["model"] == "x"
    ups = data["hooks"]["UserPromptSubmit"]
    assert ups[0]["hooks"][0]["command"] == "/home/u/.claude/hooks/other-hook.sh"
    assert ups[1]["hooks"][0]["command"] == HOOK
    for event in install.EVENTS:
        cmds = [h["command"] for g in data["hooks"][event] for h in g["hooks"]]
        assert cmds.count(HOOK) == 1, event


def test_install_is_idempotent_and_replaces_old_paths(tmp_path):
    settings = tmp_path / "settings.json"
    install.install_hooks(settings, "/old/place/platespinner-hook")
    install.install_hooks(settings, HOOK)
    install.install_hooks(settings, HOOK)
    data = json.loads(settings.read_text())
    for event in install.EVENTS:
        cmds = [h["command"] for g in data["hooks"][event] for h in g["hooks"]]
        assert cmds == [HOOK], event


def test_install_backs_up_existing_settings(tmp_path):
    settings = tmp_path / "settings.json"
    write(settings, {"a": 1})
    backup = install.install_hooks(settings, HOOK)
    assert json.loads(backup.read_text()) == {"a": 1}
    assert backup.parent == tmp_path


def test_uninstall_removes_only_ours(tmp_path):
    settings = tmp_path / "settings.json"
    other = {"hooks": [{"type": "command", "command": "/x/other-hook.sh"}]}
    write(settings, {"hooks": {"UserPromptSubmit": [other]}})
    install.install_hooks(settings, HOOK)
    install.uninstall_hooks(settings)
    assert json.loads(settings.read_text()) == {"hooks": {"UserPromptSubmit": [other]}}


def test_hooks_present(tmp_path):
    settings = tmp_path / "settings.json"
    assert install.missing_hooks(settings) == list(install.EVENTS)
    install.install_hooks(settings, HOOK)
    assert install.missing_hooks(settings) == []


def test_kitten_install(tmp_path):
    dest = install.install_kitten(tmp_path / "kitty")
    assert dest.name == "focus_with_token.py"
    assert "activation_token=token" in dest.read_text()
