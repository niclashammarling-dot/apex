"""2026-10-02: the serving process records the commit it loaded (backend/code_identity.py)."""
import subprocess

from backend.code_identity import identity_line, read_code_identity


def _repo(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "backend").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "backend" / "x.py").write_text("a = 1\n")
    (tmp_path / "data" / "live_config.json").write_text("{}\n")
    git("add", "-A")
    git("commit", "-qm", "init")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True,
                         text=True, check=True).stdout.strip()
    return sha


def test_clean_checkout_reads_its_commit_and_not_dirty(tmp_path):
    sha = _repo(tmp_path)
    assert read_code_identity(tmp_path) == {"commit": sha, "dirty": False, "dirty_files": 0}


def test_positive_control_modified_tracked_code_reads_dirty(tmp_path):
    _repo(tmp_path)
    (tmp_path / "backend" / "x.py").write_text("a = 2\n")
    ident = read_code_identity(tmp_path)
    assert ident["dirty"] is True and ident["dirty_files"] == 1


def test_runtime_config_under_data_is_not_dirty_code(tmp_path):
    _repo(tmp_path)
    (tmp_path / "data" / "live_config.json").write_text('{"max_positions": 9}\n')
    assert read_code_identity(tmp_path)["dirty"] is False


def test_no_git_reads_unknown_and_does_not_raise(tmp_path):
    ident = read_code_identity(tmp_path)
    assert ident == {"commit": None, "dirty": None, "dirty_files": None}
    assert "unknown" in identity_line(ident)


def test_ops_window_parser_reads_the_line_the_startup_writes(tmp_path):
    from backend.routers.signals_router import _SERVING_LINE
    sha = _repo(tmp_path)
    (tmp_path / "backend" / "x.py").write_text("a = 3\n")
    line = "2026-10-02 14:20:06.001 | INFO     | backend.main:lifespan:82 - " + identity_line(read_code_identity(tmp_path))
    m = _SERVING_LINE.search(line)
    assert m and sha.startswith(m.group(1)) and m.group(2) == "True"
