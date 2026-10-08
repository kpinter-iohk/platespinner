import os

from platespinner import procinfo


def fake_proc(tmp_path, procs):
    """procs: {pid: (ppid, comm, environ dict, tty_nr)}"""
    root = tmp_path / "proc"
    for pid, (ppid, comm, env, tty) in procs.items():
        d = root / str(pid)
        d.mkdir(parents=True)
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} 1 1 {tty} -1 0\n")
        (d / "comm").write_text(comm + "\n")
        (d / "environ").write_bytes(b"".join(f"{k}={v}".encode() + b"\0" for k, v in env.items()))
    return root


def test_finds_claude_ancestor_and_reads_its_kitty_env(tmp_path):
    root = fake_proc(tmp_path, {
        300: (200, "python3", {}, 0),
        200: (100, "sh", {}, 0),
        100: (50, "claude", {"KITTY_WINDOW_ID": "4", "KITTY_LISTEN_ON": "unix:/tmp/kitty-9"}, 34817),
        50: (1, "bash", {}, 34817),
    })
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    info = procinfo.inspect(300, proc_root=root, sessions=sessions)
    assert info == {"pid": 100, "kitty_window_id": "4", "kitty_listen_on": "unix:/tmp/kitty-9", "interactive": True,
                    "tmux_socket": None, "tmux_pane": None}


def test_session_file_identifies_claude_and_its_kind(tmp_path):
    root = fake_proc(tmp_path, {
        300: (100, "sh", {}, 0),
        100: (50, "node", {}, 0),
        50: (1, "bash", {}, 0),
    })
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "100.json").write_text('{"pid": 100, "kind": "interactive"}')
    info = procinfo.inspect(300, proc_root=root, sessions=sessions)
    assert info["pid"] == 100
    assert info["interactive"] is True


def test_no_tty_and_no_session_file_is_non_interactive(tmp_path):
    root = fake_proc(tmp_path, {300: (100, "sh", {}, 0), 100: (1, "claude", {}, 0)})
    info = procinfo.inspect(300, proc_root=root, sessions=tmp_path)
    assert info["interactive"] is False


def test_sdk_kind_is_non_interactive_even_with_tty(tmp_path):
    root = fake_proc(tmp_path, {300: (100, "sh", {}, 0), 100: (1, "claude", {}, 34817)})
    (tmp_path / "100.json").write_text('{"kind": "sdk"}')
    info = procinfo.inspect(300, proc_root=root, sessions=tmp_path)
    assert info["interactive"] is False


def test_no_claude_ancestor(tmp_path):
    root = fake_proc(tmp_path, {300: (1, "sh", {}, 0)})
    assert procinfo.inspect(300, proc_root=root, sessions=tmp_path) == {}


def test_git_branch_plain_repo(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/feat/nav\n")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert procinfo.git_branch(str(sub)) == "feat/nav"


def test_git_branch_worktree(tmp_path):
    gitdir = tmp_path / "main" / ".git" / "worktrees" / "w"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/topic\n")
    wt = tmp_path / "w"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {gitdir}\n")
    assert procinfo.git_branch(str(wt)) == "topic"


def test_git_branch_detached_and_missing(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n")
    assert procinfo.git_branch(str(tmp_path)) == "0123456"
    assert procinfo.git_branch("/nonexistent/path") is None


def test_pid_alive():
    assert procinfo.pid_alive(os.getpid())
    assert not procinfo.pid_alive(2 ** 22 + 12345)


def test_tmux_pane_and_socket_from_claude_env(tmp_path):
    env = {"TMUX": "/tmp/tmux-1000/default,1234,0", "TMUX_PANE": "%3", "KITTY_WINDOW_ID": "9"}
    root = fake_proc(tmp_path, {300: (100, "sh", {}, 0), 100: (1, "claude", env, 34817)})
    info = procinfo.inspect(300, proc_root=root, sessions=tmp_path)
    assert (info["tmux_socket"], info["tmux_pane"]) == ("/tmp/tmux-1000/default", "%3")
