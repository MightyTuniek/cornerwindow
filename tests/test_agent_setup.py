"""`cornerwindow agent setup`: every agent's hooks go in, stay single on a re-run, and come out
again leaving the user's own settings exactly as they were."""

import json

import pytest

from cornerwindow.platform import agent_setup as setup


@pytest.fixture
def home(tmp_path, monkeypatch):
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "COPILOT_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(setup.shutil, "which", lambda _name: None)  # found = its folder exists
    return tmp_path


def agents(home):
    return {i.id: i for i in setup.integrations(home, "/bin/cw")}


def test_claude_hooks_and_status_line_round_trip(home):
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    original = {"model": "opus", "statusLine": {"type": "command", "command": "bash ~/my line.sh", "padding": 1},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}]}}
    settings.write_text(json.dumps(original))
    claude = agents(home)["claude"]
    assert claude.found() and not claude.installed() and claude.statusline() == "other: bash ~/my line.sh"
    result = claude.apply()
    assert result.changed and "yours still shows" in result.message
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert data["statusLine"] == {"type": "command", "padding": 1,
                                  "command": "/bin/cw agent statusline --then 'bash ~/my line.sh'"}
    stop = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
    assert stop == ["notify-send done", "/bin/cw agent done --agent claude"]
    assert data["hooks"]["Notification"][0]["hooks"][0]["command"] == "/bin/cw agent waiting --agent claude"
    assert claude.installed() and claude.statusline() == "ours"
    assert not claude.apply().changed  # a second run changes nothing
    assert (home / ".claude/settings.json.cornerwindow-bak").read_text() == json.dumps(original)
    assert claude.apply(remove=True).changed
    assert json.loads(settings.read_text()) == original
    assert not claude.apply(remove=True).changed


def test_claude_status_line_choices(home):
    (home / ".claude").mkdir()
    claude = agents(home)["claude"]
    claude.apply(statusline=False)
    assert "statusLine" not in json.loads(claude.path().read_text())
    claude.apply()
    assert json.loads(claude.path().read_text())["statusLine"]["command"] == "/bin/cw agent statusline"
    claude.path().write_text(json.dumps({"statusLine": {"type": "command", "command": "mine"}}))
    claude.apply(keep_statusline=False)  # --replace-statusline
    assert json.loads(claude.path().read_text())["statusLine"]["command"] == "/bin/cw agent statusline"
    claude.path().write_text("{ // comments\n}")
    with pytest.raises(setup.Unreadable):
        claude.apply()
    assert claude.path().read_text() == "{ // comments\n}"


def test_codex_notify(home):
    config = home / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text('model = "gpt-6"\n\n[tui]\ntheme = "dark"\n')
    codex = agents(home)["codex"]
    assert codex.apply().changed
    text = config.read_text()
    assert text.startswith('model = "gpt-6"\n\nnotify = ["/bin/cw", "agent", "done", "--agent", "codex"]\n\n[tui]')
    import tomllib

    assert tomllib.loads(text)["notify"][0] == "/bin/cw" and codex.installed()
    assert not codex.apply().changed
    codex.apply(remove=True)
    assert tomllib.loads(config.read_text()) == {"model": "gpt-6", "tui": {"theme": "dark"}}
    config.write_text('notify = ["my-notifier"]\n')
    result = codex.apply()
    assert not result.changed and "skipped" in result.message and config.read_text() == 'notify = ["my-notifier"]\n'


def test_other_agents(home):
    for folder in (".gemini", ".factory", ".cursor", ".copilot", ".config/opencode"):
        (home / folder).mkdir(parents=True)
    found = agents(home)
    cursor_file = home / ".cursor/hooks.json"
    cursor_file.write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "./mine.sh"}]}}))
    for name in ("gemini", "droid", "cursor", "copilot", "opencode"):
        integration = found[name]
        assert integration.found() and not integration.installed(), name
        assert integration.apply().changed and integration.installed(), name
        assert not integration.apply().changed, name
    gemini = json.loads((home / ".gemini/settings.json").read_text())["hooks"]
    assert set(gemini) == {"BeforeAgent", "Notification", "AfterAgent"}
    cursor = json.loads(cursor_file.read_text())["hooks"]
    assert cursor["stop"] == [{"command": "./mine.sh"}, {"command": "/bin/cw agent done --agent cursor"}]
    copilot = json.loads((home / ".copilot/hooks/cornerwindow.json").read_text())
    assert copilot["version"] == 1 and copilot["hooks"]["agentStop"][0]["bash"] == "/bin/cw agent done --agent copilot"
    plugin = (home / ".config/opencode/plugins/cornerwindow.js").read_text()
    assert '"/bin/cw"' in plugin and "session.idle" in plugin
    for name in ("gemini", "droid", "cursor", "copilot", "opencode"):
        assert found[name].apply(remove=True).changed and not found[name].installed(), name
    assert json.loads(cursor_file.read_text())["hooks"] == {"stop": [{"command": "./mine.sh"}]}


def test_command_line(home, capsys):
    (home / ".claude").mkdir()
    assert setup.main(["--list"], home, "/bin/cw") == 0
    out = capsys.readouterr().out
    assert "not set up" in out and "Claude Code" in out and "not found" in out
    assert setup.main(["--all", "--dry-run"], home, "/bin/cw") == 0
    assert "would set up" in capsys.readouterr().out and not (home / ".claude/settings.json").exists()
    assert setup.main(["--yes", "--no-statusline"], home, "/bin/cw") == 0
    data = json.loads((home / ".claude/settings.json").read_text())
    assert "statusLine" not in data and "Stop" in data["hooks"]
    assert setup.main(["--agent", "codex"], home, "/bin/cw") == 0
    assert (home / ".codex/config.toml").exists()  # asked for by name: set up even before Codex made its folder
    assert setup.main(["--remove", "--yes"], home, "/bin/cw") == 0
    assert json.loads((home / ".claude/settings.json").read_text()) == {}
    assert setup.main(["--agent", "nope"], home, "/bin/cw") == 2
    # no flags and no terminal: only the status, never a question
    assert setup.main([], home, "/bin/cw", interactive=False) == 0
    assert "agent setup --all --yes" in capsys.readouterr().out


def test_wizard(home, monkeypatch, capsys):
    (home / ".claude").mkdir()
    (home / ".codex").mkdir()
    answers = iter(["", "", "n"])  # all agents, the status line, no card
    monkeypatch.setattr("builtins.input", lambda _q="": next(answers))
    monkeypatch.setattr(setup, "card_shown", lambda: False)
    assert setup.main([], home, "/bin/cw", interactive=True) == 0
    out = capsys.readouterr().out
    assert "Claude Code: set up" in out and "Codex: set up" in out
    assert json.loads((home / ".claude/settings.json").read_text())["statusLine"]["command"] == "/bin/cw agent statusline"
