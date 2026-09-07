import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from agent_system.runtime.shell import Shell


@dataclass(frozen=True)
class ProjectChanges:
    diff: str
    changed_files: List[str]
    fingerprint: str
    has_changes: bool


@dataclass(frozen=True)
class GitTreeSnapshot:
    base_commit_sha: str
    base_tree_sha: str
    tree_sha: str
    diff: str
    changed_files: List[str]
    has_changes: bool


@dataclass(frozen=True)
class PushTarget:
    commit_sha: str
    local_ref: str
    remote: str
    remote_url: str
    target_ref: str


EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class Git:
    def __init__(self, root: Path = None):
        self.root = (Path(root or Path.cwd())).resolve()
        self.repo_root = self.root
        self.shell = Shell(self.root)

    def ensure_runtime_excludes(self):
        git_dir = self.root / ".git"
        if not git_dir.exists() or not git_dir.is_dir():
            return
        exclude = git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        needed = [".agent/", "__pycache__/", "*.pyc"]
        to_add = [l for l in needed if l not in existing]
        if to_add:
            with exclude.open("a", encoding="utf-8") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                for l in to_add:
                    f.write(l + "\n")

    def is_workspace_repo(self) -> bool:
        return self._check_workspace_repo()

    def ensure_runtime_isolation(self):
        if not self._check_workspace_repo():
            return False
        self.ensure_runtime_excludes()
        r = self.shell.run("git ls-files -- .agent")
        if r.returncode != 0:
            return False
        tracked = r.stdout.strip()
        if not tracked:
            return True
        print("Agent runtime files were tracked by Git. Removing .agent from the repository index.")
        self.shell.run("git rm -r --cached --ignore-unmatch .agent")
        r2 = self.shell.run("git ls-files -- .agent")
        if r2.returncode == 0 and r2.stdout.strip():
            print(f"  Warning: still tracked: {r2.stdout.strip()[:80]}")
        return True

    def _check_workspace_repo(self) -> bool:
        r = self.shell.run("git rev-parse --show-toplevel")
        if r.returncode != 0:
            return False
        found = Path(r.stdout.strip()).resolve()
        return found == self.root

    def is_supported_layout(self) -> bool:
        gd = self.root / ".git"
        if gd.is_file():
            return False
        return True

    def _guard(self) -> bool:
        if not self._check_workspace_repo():
            return False
        return True

    def head_sha(self) -> str:
        if not self._guard():
            return ""
        r = self.shell.run(["git", "rev-parse", "HEAD"])
        return r.stdout.strip() if r.returncode == 0 else ""

    def head_ref(self) -> str:
        if not self._guard():
            return ""
        r = self.shell.run(["git", "symbolic-ref", "--quiet", "HEAD"])
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        return ""

    def commit_parent(self, sha: str = "HEAD") -> str:
        if not sha:
            return ""
        if not self._guard():
            return ""
        r = self.shell.run(["git", "rev-parse", f"{sha}^"])
        return r.stdout.strip() if r.returncode == 0 else ""

    def commit_subject(self, sha: str = "HEAD") -> str:
        if not sha:
            return ""
        if not self._guard():
            return ""
        r = self.shell.run(["git", "log", "-1", "--format=%s", sha])
        return r.stdout.strip() if r.returncode == 0 else ""

    def commit_tree_sha(self, sha: str) -> str:
        if not sha:
            return ""
        r = self.shell.run(["git", "rev-parse", f"{sha}^{{tree}}"])
        return r.stdout.strip() if r.returncode == 0 else ""

    def head_tree_sha(self) -> str:
        if not self._guard():
            return ""
        r = self.shell.run(["git", "rev-parse", "HEAD^{tree}"])
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        return EMPTY_TREE_SHA

    def diff(self, args: str = "") -> str:
        if not self._guard():
            return ""
        if args:
            base = f"git diff {args}".strip()
            r = self.shell.run(base)
            return r.stdout
        return self.project_changes()

    def project_changes(self) -> str:
        if not self._guard():
            return ""
        r = self.shell.run("git diff HEAD")
        out = r.stdout
        u = self.shell.run("git ls-files --others --exclude-standard")
        if u.stdout.strip():
            for f in u.stdout.strip().splitlines():
                f = f.strip()
                if f:
                    p = self.root / f
                    if p.is_file():
                        try:
                            content = p.read_text(encoding="utf-8")
                            out += f"\nnew file: {f}\n{content[:3000]}\n"
                        except Exception:
                            out += f"\nnew file: {f}\n"
                    else:
                        out += f"\nnew file: {f}\n"
        return out

    def diff_stat(self, args: str = "") -> str:
        if not self._guard():
            return ""
        base = f"git diff --stat {args}".strip() if args else "git diff --stat"
        r = self.shell.run(base)
        out = r.stdout
        ls_args = args.strip() if args.strip() else ""
        u = self.shell.run(f"git ls-files --others --exclude-standard -- {ls_args}".strip() if ls_args else "git ls-files --others --exclude-standard")
        if u.stdout.strip():
            out += "\n[untracked] " + u.stdout.strip()
        return out

    def status(self) -> str:
        if not self._guard():
            return ""
        r = self.shell.run("git status --porcelain -uall")
        return r.stdout

    def project_diff(self) -> str:
        raw = self.diff()
        if not raw:
            return ""
        lines = []
        skip = False
        for l in raw.splitlines():
            if l.startswith("diff --git") and ".agent/" in l:
                skip = True
                continue
            if l.startswith("diff --git"):
                skip = False
            if skip:
                continue
            if ".agent/" in l or "__pycache__" in l or ".pyc" in l:
                continue
            lines.append(l)
        return "\n".join(lines)

    def snapshot_worktree_tree(self) -> str:
        if not self._guard():
            return ""
        self.ensure_runtime_excludes()
        import tempfile as _tf
        tmp_dir = Path(_tf.mkdtemp(prefix="workflow-index-"))
        tmp_index = tmp_dir / "index"
        head = self.head_sha()
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(tmp_index)
        shell = Shell(self.root)
        if head:
            r = shell.run(["git", "read-tree", "HEAD"], env=env)
            if r.returncode != 0:
                try:
                    import shutil
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass
                return ""
        else:
            shell.run(["git", "read-tree", "--empty"], env=env)
        shell.run(["git", "add", "-A", "--", "."], env=env)
        wr = shell.run(["git", "write-tree"], env=env)
        tree = ""
        if wr.returncode == 0 and wr.stdout.strip():
            tree = wr.stdout.strip()
        else:
            wr2 = shell.run(["git", "write-tree"], env=env)
            if wr2.returncode == 0 and wr2.stdout.strip():
                tree = wr2.stdout.strip()
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
        return tree

    def diff_between_trees(self, base_tree: str, target_tree: str) -> str:
        if not base_tree:
            base_tree = EMPTY_TREE_SHA
        if not target_tree:
            return ""
        r = self.shell.run(["git", "diff", "--binary", "--full-index", "--no-ext-diff", base_tree, target_tree])
        if r.returncode != 0:
            return ""
        return r.stdout

    def changed_files_between_trees(self, base_tree: str, target_tree: str) -> List[str]:
        if not base_tree:
            base_tree = EMPTY_TREE_SHA
        if not target_tree:
            return []
        r = self.shell.run(["git", "diff", "--name-only", base_tree, target_tree])
        if r.returncode != 0:
            return []
        files = []
        for l in r.stdout.splitlines():
            f = l.strip()
            if f and ".agent/" not in f and "__pycache__" not in f and not f.endswith(".pyc"):
                files.append(f)
        return sorted(files)

    def capture_tree_snapshot(self) -> GitTreeSnapshot:
        head = self.head_sha()
        base_tree = self.head_tree_sha() if head else EMPTY_TREE_SHA
        tree = self.snapshot_worktree_tree()
        if not tree:
            tree = base_tree
        diff = self.diff_between_trees(base_tree, tree)
        files = self.changed_files_between_trees(base_tree, tree)
        has = bool(diff.strip()) or bool(files) or tree != base_tree
        return GitTreeSnapshot(base_commit_sha=head or "", base_tree_sha=base_tree, tree_sha=tree, diff=diff, changed_files=files, has_changes=has)

    def create_commit_object(self, tree_sha: str, parent_sha: str, message: str) -> str:
        if not tree_sha or not message:
            return ""
        tmp = None
        try:
            import tempfile
            fd, tmppath = tempfile.mkstemp(suffix=".commitmsg")
            os.write(fd, message.encode("utf-8"))
            os.close(fd)
            tmp = tmppath
            args = ["git", "commit-tree", tree_sha]
            if parent_sha:
                args += ["-p", parent_sha]
            args += ["-F", tmp]
            r = self.shell.run(args)
            if r.returncode != 0:
                return ""
            return r.stdout.strip()
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass

    def update_ref(self, ref: str, new_sha: str, old_sha: str = None) -> bool:
        if not ref or not new_sha:
            return False
        if old_sha:
            r = self.shell.run(["git", "update-ref", ref, new_sha, old_sha])
        else:
            r = self.shell.run(["git", "update-ref", ref, new_sha])
        return r.returncode == 0

    def sync_index_to_head(self):
        if not self._guard():
            return False
        head = self.head_sha()
        if not head:
            return False
        r = self.shell.run(["git", "read-tree", head])
        return r.returncode == 0

    def resolve_push_target(self, commit_sha: str = None) -> Optional[PushTarget]:
        commit_sha = commit_sha or self.head_sha()
        if not commit_sha:
            return None
        local_ref = self.head_ref()
        if not local_ref:
            return None
        remote = ""
        # check upstream
        r = self.shell.run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
        if r.returncode == 0 and r.stdout.strip():
            upstream = r.stdout.strip()
            if "/" in upstream:
                remote = upstream.split("/")[0]
        if not remote:
            if self.has_remote():
                remote = "origin"
            else:
                return None
        url = ""
        rr = self.shell.run(["git", "remote", "get-url", remote])
        if rr.returncode == 0:
            url = rr.stdout.strip()
        target_ref = local_ref
        return PushTarget(commit_sha=commit_sha, local_ref=local_ref, remote=remote, remote_url=url, target_ref=target_ref)

    def push_exact(self, target: PushTarget) -> dict:
        if not target:
            return {"status": "FAILED", "message": "no push target"}
        if not self.has_remote():
            return {"status": "NO_REMOTE", "message": "no remote configured"}
        cur_url = ""
        rr = self.shell.run(["git", "remote", "get-url", target.remote])
        if rr.returncode == 0:
            cur_url = rr.stdout.strip()
        if target.remote_url and cur_url != target.remote_url:
            return {"status": "REMOTE_FAILED", "message": f"remote URL mismatch: {target.remote_url} != {cur_url}"}
        head = self.head_sha()
        if head != target.commit_sha:
            return {"status": "REMOTE_FAILED", "message": f"HEAD mismatch: {head} != {target.commit_sha}"}
        lr = self.head_ref()
        if lr != target.local_ref:
            return {"status": "REMOTE_FAILED", "message": f"local ref mismatch: {lr} != {target.local_ref}"}
        refspec = f"{target.commit_sha}:{target.target_ref}"
        r = self.shell.run(["git", "push", "--no-verify", target.remote, refspec])
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            return {"status": "REMOTE_FAILED", "message": out[:500]}
        vr = self.shell.run(["git", "ls-remote", target.remote, target.target_ref])
        if vr.returncode != 0:
            return {"status": "REMOTE_FAILED", "message": f"ls-remote failed: {vr.stdout+vr.stderr}"}
        remote_sha = ""
        for line in vr.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == target.target_ref:
                remote_sha = parts[0]
                break
            if len(parts) >= 1:
                remote_sha = parts[0]
        if remote_sha != target.commit_sha:
            return {"status": "REMOTE_FAILED", "message": f"remote ref mismatch: {remote_sha} != {target.commit_sha}"}
        return {"status": "SUCCESS", "message": out[:500]}

    def commit(self, message: str) -> dict:
        # Legacy wrapper: create snapshot commit via commit-tree + update-ref to avoid hooks
        if not message or not message.strip():
            return {"status": "FAILED", "returncode": 1, "sha": None, "message": "empty message"}
        if not self._guard():
            return {"status": "FAILED", "returncode": 1, "sha": None, "message": "not a git repository"}
        snap = self.capture_tree_snapshot()
        if not snap.has_changes:
            return {"status": "FAILED", "returncode": 1, "sha": self.head_sha() or None, "message": "no changes to commit"}
        head = snap.base_commit_sha
        ref = self.head_ref()
        if not ref:
            return {"status": "FAILED", "returncode": 1, "sha": None, "message": "detached HEAD not supported"}
        pending = self.create_commit_object(snap.tree_sha, head, message)
        if not pending:
            return {"status": "FAILED", "returncode": 1, "sha": None, "message": "commit-tree failed"}
        # verify
        tree_check = self.commit_tree_sha(pending)
        if tree_check != snap.tree_sha:
            return {"status": "FAILED", "returncode": 1, "sha": pending, "message": f"commit tree mismatch: {tree_check} != {snap.tree_sha}"}
        ok = self.update_ref(ref, pending, head) if head else self.update_ref(ref, pending)
        if not ok:
            return {"status": "FAILED", "returncode": 1, "sha": pending, "message": "update-ref failed (branch changed externally?)"}
        self.sync_index_to_head()
        return {"status": "SUCCESS", "returncode": 0, "sha": pending, "message": ""}

    def commit_diff(self, sha: str) -> str:
        if not sha:
            return ""
        if not self._guard():
            return ""
        r = self.shell.run(["git", "show", "--format=", "--find-renames", sha])
        if r.stdout.strip():
            return r.stdout + r.stderr
        r2 = self.shell.run(["git", "show", sha])
        if r2.stdout:
            return (r2.stdout + r2.stderr)[:8000]
        return ""

    def has_commits(self) -> bool:
        if not self._guard():
            return False
        r = self.shell.run(["git", "rev-parse", "HEAD"])
        return r.returncode == 0

    def has_remote(self) -> bool:
        if not self._guard():
            return False
        r = self.shell.run(["git", "remote"])
        return bool(r.stdout.strip())

    def remote_url(self) -> str:
        if not self._guard():
            return ""
        r = self.shell.run(["git", "remote", "get-url", "origin"])
        return r.stdout.strip() if r.returncode == 0 else ""

    def push(self) -> dict:
        # Deprecated: use push_exact
        if not self.has_remote():
            return {"status": "NO_REMOTE", "message": "no remote configured"}
        r = self.shell.run(["git", "push"])
        out = r.stdout + r.stderr
        if r.returncode == 0:
            return {"status": "SUCCESS", "message": out.strip()}
        return {"status": "REMOTE_FAILED", "message": out.strip()}

    def _quote(self, s: str) -> str:
        return "'" + s.replace("'", "'\"'\"'") + "'"

    def changed_files(self) -> list:
        if not self._guard():
            return []
        tracked = self.shell.run(["git", "diff", "--name-only", "HEAD"]).stdout if self.head_sha() else ""
        untracked = self.shell.run(["git", "ls-files", "--others", "--exclude-standard"]).stdout
        files = set()
        for line in tracked.splitlines():
            f = line.strip()
            if f and ".agent/" not in f and "__pycache__" not in f and not f.endswith(".pyc"):
                files.add(f)
        for line in untracked.splitlines():
            f = line.strip()
            if f and ".agent/" not in f and "__pycache__" not in f and not f.endswith(".pyc"):
                files.add(f)
        return sorted(files)

    def project_changes_model(self) -> ProjectChanges:
        raw = self.project_changes()
        files = self.changed_files()
        return ProjectChanges(diff=raw, changed_files=files, fingerprint=hashlib.sha256(raw.encode()).hexdigest(), has_changes=bool(raw.strip()))
