import subprocess
from pathlib import Path

from agent_system.planning.repository_map import build_repository_map


def test_repository_map_includes_src_and_tests(tmp_path):
    # Create a realistic repo structure
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("x=1")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").write_text("def test_foo(): pass")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "README.md").write_text("# Readme")
    # Create .agent garbage that should be excluded
    (tmp_path / ".agent" / "runtime").mkdir(parents=True)
    (tmp_path / ".agent" / "runtime" / "junk.json").write_text('{"x":1}')
    # Init git so build_repository_map can find tracked files
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    m = build_repository_map(tmp_path, max_files=500, max_chars=12000)
    assert "src/foo.py" in m or "src" in m
    assert "pyproject" in m.lower() or "pyproject.toml" in m
    assert ".agent" not in m or "junk" not in m
    # length control
    m2 = build_repository_map(tmp_path, max_chars=200)
    assert len(m2) <= 250  # includes truncation marker


def test_repository_map_excludes_agent_runtime(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a")
    (tmp_path / ".agent" / "debug" / "planner").mkdir(parents=True)
    (tmp_path / ".agent" / "debug" / "planner" / "skeleton.json").write_text("{}")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
    m = build_repository_map(tmp_path)
    assert "debug/planner" not in m
