from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class MilestoneContext:
    task: str
    plan: str
    diff: str
    git_log: str
    previous_milestones: List[str]
    human_feedback: Optional[str] = None
    repo_state: str = ""


def collect_context(root: Path = None, feedback: str = None) -> MilestoneContext:
    from agent_system.runtime.shell import Shell

    root = Path(root or Path.cwd()).resolve()
    # Prefer frozen session provenance
    frozen_task = ""
    frozen_plan = ""
    diff = ""
    git_log = ""
    start_sha = ""
    end_sha = ""
    try:
        from agent_system.runtime.state_store import StateManager as _SM
        from agent_system.supervisor.session import SessionManager as _Sess
        st = _SM(root).load()
        sid = st.get("session_id")
        if sid:
            sess = _Sess(root).get(sid)
            if sess:
                frozen_task = sess.get("task", "") or ""
                start_sha = sess.get("start_head_sha", "") or st.get("workflow_start_sha", "") or ""
            pd, pt = _Sess(root).get_plan(sid)
            if pt and pt.strip():
                frozen_plan = pt
            elif pd:
                try:
                    from agent_system.plan_parser import render_plan_md
                    frozen_plan = render_plan_md(pd)
                except Exception:
                    frozen_plan = ""
            # result end sha
            try:
                import json
                res_path = root / ".agent" / "runtime" / sid / "result.json"
                if res_path.exists():
                    res = json.loads(res_path.read_text(encoding="utf-8"))
                    end_sha = res.get("end_head_sha", "") or ""
                    if not start_sha:
                        start_sha = res.get("start_head_sha", "") or ""
            except Exception:
                pass
    except Exception:
        pass

    # Fallback to load_context if no frozen
    if not frozen_task or not frozen_plan:
        from agent_system.context import load_context
        ctx0 = load_context(root)
        if not frozen_task:
            frozen_task = ctx0.task
        if not frozen_plan:
            frozen_plan = ctx0.plan

    shell = Shell(root)
    if start_sha and end_sha:
        from agent_system.runtime.git import Git
        git = Git(root)
        diff = git.diff_between_trees(git.shell.run(["git", "rev-parse", f"{start_sha}^{{tree}}"]).stdout.strip() if start_sha else "", git.shell.run(["git", "rev-parse", f"{end_sha}^{{tree}}"]).stdout.strip() if end_sha else "")
        if not diff:
            # fallback
            diff = shell.run(f"git diff --binary --full-index {start_sha}..{end_sha} 2>&1").stdout if start_sha and end_sha else ""
        git_log = shell.run(f"git log --oneline {start_sha}..{end_sha} 2>&1").stdout if start_sha and end_sha else shell.run("git log --oneline -10 2>&1").stdout
    else:
        from agent_system.runtime.git import Git
        git = Git(root)
        diff = git.diff_between_trees(git.head_tree_sha() if git.head_sha() else "", git.head_tree_sha()) if False else shell.run("git log --oneline -10 2>&1").stdout
        # Use regular diff if no provenance
        from agent_system.runtime.git import Git as _G2
        g2 = _G2(root)
        if start_sha and end_sha:
            diff = shell.run(f"git diff --binary --full-index {start_sha}..{end_sha} 2>&1").stdout
            git_log = shell.run(f"git log --oneline {start_sha}..{end_sha} 2>&1").stdout
        else:
            diff = shell.run("git diff HEAD 2>&1 | head -200").stdout
            git_log = shell.run("git log --oneline -10 2>&1").stdout

    milestones_dir = root / ".agent" / "milestones"
    previous: List[str] = []
    if milestones_dir.exists():
        for f in sorted(milestones_dir.glob("*.md")):
            try:
                previous.append(f.read_text(encoding="utf-8"))
            except Exception:
                pass

    repo_state = ""
    try:
        repo_state = shell.run("git status --porcelain 2>&1").stdout
        repo_state += "\n" + shell.run("ls -la src/ 2>&1 | head -20").stdout
    except Exception:
        pass

    fb_file = root / ".agent" / "human_feedback.md"
    if not feedback and fb_file.exists():
        try:
            feedback = fb_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    return MilestoneContext(
        task=frozen_task,
        plan=frozen_plan,
        diff=diff,
        git_log=git_log,
        previous_milestones=previous,
        human_feedback=feedback,
        repo_state=repo_state,
    )
