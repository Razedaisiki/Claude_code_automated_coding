from pathlib import Path

from agent_system.agents.models import AgentResult
from agent_system.agents.parent import ParentAgent
from agent_system.config import resolve_api_key, resolve_base_url, resolve_model
from agent_system.context import ProjectContext, load_context
from agent_system.plan_parser import parse_plan
from agent_system.runtime.git import Git


def _load_prompt(name: str) -> str:
    base = Path(__file__).parent.parent / "prompts"
    candidates = [
        base / f"{name}.md",
        base / name / "system.md",
        base / name / "planning.md",
    ]
    parts = []
    for p in candidates:
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    if parts:
        common = base / "common" / "engineering_rules.md"
        if common.exists():
            parts.append(common.read_text(encoding="utf-8"))
        return "\n\n".join(parts)
    return ""


class ClaudeParentAgent(ParentAgent):
    def __init__(self, root: Path = None, model: str = None):
        self.root = root or Path.cwd()
        self.model = model or resolve_model()
        self.git = Git(self.root)

    def run(self, task: str) -> AgentResult:
        print("Starting Claude Parent")
        ctx = load_context(self.root)
        print("  Loading context")
        if ctx.task:
            print("  TASK analyzed")
        if ctx.instructions:
            print("  CLAUDE.md loaded")
        if ctx.milestones:
            print("  milestones loaded")

        plan_text = self._plan(ctx.task or task, ctx)
        plan_file = self.root / ".agent" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(plan_text, encoding="utf-8")
        print("  plan.md created")

        tasks = parse_plan(plan_text)
        if tasks:
            print(f"  parsed {len(tasks)} tasks from plan")
            for t in tasks:
                print(f"    {t.id} [{t.role}] {t.description}")

            from agent_system.agents.code_agent import CodeAgent
            from agent_system.agents.models import AgentTask
            from agent_system.agents.subagent import MockTestAgent
            from agent_system.supervisor.state import StateManager as _SM

            _st = _SM(self.root).load()
            start_idx = 0
            if isinstance(_st.get("delivery", {}).get("task_index"), int):
                start_idx = _st["delivery"]["task_index"] + 1
                if 0 < start_idx < len(tasks):
                    print(f"  Resuming from task index {start_idx}")
                elif start_idx >= len(tasks):
                    print("  All tasks already completed, skipping execution")
                    tasks = []
                else:
                    start_idx = 0
            elif _st.get("delivery", {}).get("task_id"):
                done_id = _st["delivery"]["task_id"]
                ci_s = _st["delivery"].get("ci_status", "")
                if ci_s in ("CI_PASSED", "CI_NOT_DETECTED", "APPROVED_WITH_NOTE", "CI_NOT_CONFIGURED"):
                    for _idx, _t in enumerate(tasks):
                        if _t.id == done_id:
                            start_idx = _idx + 1
                            break
                    if start_idx > 0:
                        print(f"  Resuming from task index {start_idx} (by task_id)")
            tasks = tasks[start_idx:]

            for task_index, t in enumerate(tasks, start=start_idx):
                print(f"  Dispatch: {t.id} -> {t.role}")
                last_diff = ""
                for attempt in range(1, 4):
                    diff_before = self.git.diff()
                    if t.role == "code":
                        if attempt > 1:
                            t = AgentTask(id=t.id, role=t.role, description=t.description + f"\n[Retry {attempt}: previous review failed: {last_reason}]", files=t.files)
                        result = CodeAgent(root=self.root, model=self.model).execute(t)
                    else:
                        result = MockTestAgent().execute(t)

                    diff_after = self.git.diff()
                    review = self._review(t, result, diff_before, diff_after)
                    if review.status == "SUCCESS":
                        print(f"  Review PASSED for {t.id} (attempt {attempt})")
                        if t.type == "implementation" and review.commit_message:
                            from agent_system.delivery import DeliveryConfig
                            from agent_system.runtime.delivery_runtime import DeliveryRuntime
                            from agent_system.supervisor.state import StateManager

                            delivery = DeliveryRuntime(self.root)
                            msg = review.commit_message
                            delivery.commit(msg)
                            print(f"  Committed: {msg}")
                            r = self.git.shell.run("git rev-parse HEAD 2>&1")
                            sha = r.stdout.strip() if r.returncode == 0 else None
                            cfg = DeliveryConfig.load(self.root)
                            if cfg.mode == "gh" and sha:
                                push_res = delivery.push(commit_sha=sha)
                                if push_res["status"] == "SUCCESS":
                                    print(f"  Pushed: {push_res['message'][:80]}")
                                elif push_res["status"] in ("REMOTE_FAILED", "NO_REMOTE"):
                                    label = "Push failed (fallback to local)" if push_res["status"] == "REMOTE_FAILED" else "No remote, skipping CI"
                                    print(f"  {label}: {push_res['message'][:80]}")
                                    continue
                                else:
                                    print(f"  Push skipped: {push_res['message'][:80]}")
                                    continue
                                StateManager(self.root).update(status="WAITING_CI", delivery={"mode": "gh", "commit_sha": sha, "task_id": t.id, "task_index": task_index})
                                print(f"  WAITING_CI for {sha[:7]}")
                                ci_res = delivery.wait_ci(sha)
                                ci_status = ci_res.get("status", "CI_NOT_DETECTED")
                                if ci_status == "CI_PASSED":
                                    StateManager(self.root).update(status="RUNNING", delivery={"mode": "gh", "commit_sha": sha, "ci_status": "CI_PASSED", "task_id": t.id, "task_index": task_index})
                                    print("  CI_PASSED")
                                elif ci_status == "CI_FAILED":
                                    print("  CI_FAILED")
                                    ci_decision = self.ci_review(ci_status="CI_FAILED", ci_logs=ci_res.get("failed_logs", ""), commit_sha=sha)
                                    if ci_decision["decision"] == "CHANGES_REQUIRED":
                                        StateManager(self.root).update(status="RUNNING", delivery={"mode": "gh", "commit_sha": sha, "ci_status": "CI_FAILED", "task_id": t.id, "task_index": task_index})
                                        correction = ci_decision.get("correction", "")[:500]
                                        print(f"  CI correction needed: {correction[:80]}")
                                        t = AgentTask(id=t.id, role=t.role, description=correction or t.description, files=t.files)
                                        last_reason = ci_decision["reason"]
                                        continue
                                    else:
                                        StateManager(self.root).update(status="RUNNING", delivery={"mode": "gh", "commit_sha": sha, "ci_status": ci_decision["decision"], "task_id": t.id, "task_index": task_index})
                                elif ci_status == "CI_NOT_DETECTED":
                                    print("  No CI runs detected, continuing")
                                    StateManager(self.root).update(status="RUNNING", delivery={"mode": "gh", "commit_sha": sha, "ci_status": "CI_NOT_DETECTED", "task_id": t.id, "task_index": task_index})
                                else:
                                    StateManager(self.root).update(status="RUNNING", delivery={"mode": "gh", "commit_sha": sha, "task_id": t.id, "task_index": task_index})
                            else:
                                dres = delivery.deliver(commit_sha=sha)
                                if dres.push_status == "SUCCESS":
                                    print(f"  Pushed: {dres.push_message[:80]}")
                                elif dres.push_status == "REMOTE_FAILED":
                                    print(f"  Push failed (fallback to local): {dres.push_message[:80]}")
                                elif dres.push_status == "NO_REMOTE":
                                    print("  Local mode: no remote")
                                elif dres.push_status == "SKIPPED":
                                    print("  Local delivery: committed")
                        break
                    cur_diff = diff_after[len(diff_before):] if diff_after.startswith(diff_before) else diff_after
                    if cur_diff.strip() == last_diff.strip() and attempt > 1:
                        print(f"  No new changes (same diff), stopping retry for {t.id}")
                        return review
                    last_diff = cur_diff
                    last_reason = review.message
                    print(f"  Review FAILED for {t.id} (attempt {attempt}): {review.message}")
                    if attempt == 3:
                        return review
                    print(f"  Retrying {t.id}...")

        else:
            self._execute(plan_text, ctx)

        print("Parent finished")
        return AgentResult(
            status="SUCCESS",
            message="plan executed",
            artifacts=[str(plan_file)],
            next_action="DONE",
        )

    def get_context(self) -> ProjectContext:
        return load_context(self.root)

    def ci_review(self, ci_status: str, ci_logs: str = "", task: str = None, commit_sha: str = None) -> dict:
        p = Path(__file__).parent.parent / "prompts" / "parent" / "ci_review.md"
        sys_text = p.read_text(encoding="utf-8") if p.exists() else _load_prompt("parent/ci_review") or "You are the Tech Lead reviewing CI results."
        from agent_system.context import load_context

        ctx = load_context(self.root)
        from agent_system.runtime.git import Git

        git = Git(self.root)
        diff = git.commit_diff(commit_sha) if commit_sha else git.diff()
        user = (
            f"Task:\n{task or ctx.task}\n\n"
            f"Plan:\n{ctx.plan[:3000]}\n\n"
            f"Commit: {commit_sha or ''}\n\n"
            f"Diff:\n{diff[:4000]}\n\n"
            f"CI status: {ci_status}\n\nCI logs:\n{ci_logs[:4000]}"
        )
        text = self._invoke(sys_text, user)
        try:
            import json

            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                decision = data.get("decision", "")
                if decision in ("APPROVED", "NO_CODE_CHANGE", "APPROVED_WITH_NOTE"):
                    return {"decision": "APPROVED_WITH_NOTE" if decision == "NO_CODE_CHANGE" else decision, "reason": data.get("reason", text[:800]), "classification": data.get("classification", ""), "correction": data.get("correction", "")}
                if decision == "CHANGES_REQUIRED":
                    corr = data.get("correction", {})
                    if isinstance(corr, dict):
                        desc = corr.get("description", "")
                        return {"decision": "CHANGES_REQUIRED", "reason": data.get("reason", text[:800]), "correction": desc or str(corr), "classification": "CHANGE_RELATED"}
                    return {"decision": "CHANGES_REQUIRED", "reason": data.get("reason", text[:800]), "correction": str(corr) if corr else text}
        except Exception:
            pass
        if "APPROVE" in text.upper() or "CI_APPROVED" in text.upper():
            return {"decision": "APPROVED", "reason": text[:800]}
        low = text.lower()
        if "existing_project" in low or "infrastructure" in low:
            return {"decision": "APPROVED_WITH_NOTE", "reason": text[:800], "classification": text[:300]}
        return {"decision": "CHANGES_REQUIRED", "reason": text[:800], "correction": text}

    def create_milestone(self, feedback: str = None) -> str:
        from datetime import datetime, timezone

        from agent_system.milestone.context import collect_context

        ctx = collect_context(self.root, feedback=feedback)
        p = Path(__file__).parent.parent / "prompts" / "parent" / "historian.md"
        system = p.read_text(encoding="utf-8") if p.exists() else _load_prompt("parent/historian") or "You are the Engineering Historian. Create a milestone document."
        prev_text = "\n\n---\n\n".join(ctx.previous_milestones[-2:]) if ctx.previous_milestones else "(none)"
        user = (
            f"Task:\n{ctx.task[:4000]}\n\n"
            f"Plan:\n{ctx.plan[:4000]}\n\n"
            f"Diff:\n{ctx.diff[:6000]}\n\n"
            f"Git log:\n{ctx.git_log[:2000]}\n\n"
            f"Repo state:\n{ctx.repo_state[:2000]}\n\n"
            f"Previous milestones (last 2):\n{prev_text[:4000]}\n\n"
            f"Human feedback:\n{ctx.human_feedback or '(none)'}"
        )
        content = self._invoke(system, user)
        if content.startswith("mock result"):
            content = (
                f"# Milestone {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
                f"## Objective\n{ctx.task[:200] or 'Completed task'}\n\n"
                f"## Implementation Summary\n{content}\n\n"
                f"## Architecture Decisions\nSee plan and diff.\n\n"
                f"## Challenges\nNone noted.\n\n"
                f"## Future Considerations\nContinue iteration.\n"
            )
        milestones_dir = self.root / ".agent" / "milestones"
        milestones_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(milestones_dir.glob("*.md"))
        next_id = len(existing) + 1
        fname = f"{next_id:03d}.md"
        out = milestones_dir / fname
        out.write_text(content if content.lstrip().startswith("#") else f"# Milestone {next_id:03d}\n\n{content}", encoding="utf-8")
        return str(out)

    def _review(self, task, result: AgentResult, diff_before: str = "", diff_after: str = "") -> AgentResult:
        if result.status == "FAILED":
            return AgentResult(status="FAILED", message=f"task {task.id} failed: {result.message}", artifacts=result.artifacts)
        if not result.message:
            return AgentResult(status="FAILED", message=f"task {task.id} produced empty result", artifacts=result.artifacts)
        if task.type == "verification":
            return AgentResult(status="SUCCESS", message=f"task {task.id} accepted (verification)", artifacts=result.artifacts)
        if task.type == "optional":
            return AgentResult(status="SUCCESS", message=f"task {task.id} accepted (optional)", artifacts=result.artifacts)
        if task.role != "code":
            return AgentResult(status="SUCCESS", message=f"task {task.id} accepted (non-code)", artifacts=result.artifacts)
        raw_diff = diff_after[len(diff_before):] if diff_after.startswith(diff_before) else diff_after
        filtered = "\n".join(l for l in raw_diff.splitlines() if "__pycache__" not in l and ".pyc" not in l)
        diff = filtered.strip()
        if not diff and not result.artifacts:
            return AgentResult(status="FAILED", message=f"task {task.id} produced no file changes", artifacts=result.artifacts)
        if not diff and result.artifacts:
            diff = "\n".join(result.artifacts)
        ctx = load_context(self.root)
        score = self._llm_review(task, result, diff, ctx)
        if score is not None and not score.get("pass", True):
            return AgentResult(status="FAILED", message=f"task {task.id} review failed: {score.get('reason','')}", artifacts=result.artifacts)
        if diff.strip():
            print(f"    diff: {diff[:200]}")
        cm = self._commit_message(task, diff)
        return AgentResult(status="SUCCESS", message=f"task {task.id} accepted", artifacts=result.artifacts, commit_message=cm)

    def generate_commit_message(self, diff: str, hint: str = "") -> str:
        p = Path(__file__).parent.parent / "prompts" / "parent" / "commit_message.md"
        system = p.read_text(encoding="utf-8") if p.exists() else _load_prompt("parent/commit_message") or "Generate a commit message."
        user = f"Diff:\n{diff[:6000]}\n\nHint:\n{hint[:1000]}" if hint else f"Diff:\n{diff[:6000]}"
        text = self._invoke(system, user)
        if text.startswith("mock result"):
            return "chore: preserve existing changes"
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        if lines:
            msg = lines[0].strip('"').strip("'").strip()
            if msg:
                return msg
        return "chore: preserve existing changes"

    def _commit_message(self, task, diff: str) -> str:
        return self.generate_commit_message(diff, hint=task.description)

    def _llm_review(self, task, result: AgentResult, diff: str, ctx: ProjectContext):
        try:
            import anthropic, json

            api_key = resolve_api_key()
            if not api_key:
                return None
            base_url = resolve_base_url()
            client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=15) if base_url else anthropic.Anthropic(api_key=api_key, timeout=15)
            system = "You are a code reviewer. The workspace is demo/ (so demo/src/demo_app.py is the correct path for task src/demo_app.py). Judge if the diff satisfies the task and plan. Reply JSON only: {\"pass\": bool, \"reason\": string}"
            user = f"Task: {task.description}\nPlan excerpt: {ctx.plan[:1500]}\nDiff:\n{diff[:3000]}\nResult: {result.message[:500]}\nNote: workspace is demo/, so paths like demo/src/demo_app.py and src/demo_app.py refer to the same file."
            resp = client.messages.create(
                model=self.model or "claude-sonnet-4-20250514",
                max_tokens=512,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except Exception:
            return None
        return None

    def _plan(self, task: str, ctx: ProjectContext) -> str:
        system = _load_prompt("parent")
        milestones_text = "\n\n".join(f"## {m.name}\n{m.content}" for m in ctx.milestones)
        user = f"Task:\n{task}\n\nRepo: {ctx.repository}\n\nCLAUDE.md:\n{ctx.instructions[:2000]}\n\nMilestones:\n{milestones_text[:2000]}"
        invoked = self._invoke(system, user)
        if invoked and not invoked.startswith("mock result"):
            return invoked if invoked.lstrip().startswith("#") else f"# Plan\n\n{invoked}\n"
        first_line = task.strip().splitlines()[0] if task.strip() else "No task"
        return (
            f"# Objective\n{first_line}\n\n"
            f"# Analysis\nTask: {task.strip()[:500]}\nRepo: {ctx.repository or 'unknown'}\n\n"
            f"# Tasks\n1. Implement feature\n2. Add tests\n\n"
            f"# Execution Order\n1. Implement\n2. Test\n\n"
            f"# Validation\nRun demo workflow and verify output.\n"
        )

    def _execute(self, plan: str, ctx: ProjectContext):
        print("  execute phase")
        print("  reading plan")
        if ctx.task:
            print(f"  applying plan for: {ctx.task.strip().splitlines()[0][:60]}")
        print("  tools: read / write / bash (mock)")

    def _invoke(self, system: str, user: str) -> str:
        try:
            import anthropic

            api_key = resolve_api_key()
            if not api_key:
                return self._fallback(user)

            base_url = resolve_base_url()
            if base_url:
                client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=20)
            else:
                client = anthropic.Anthropic(api_key=api_key, timeout=20)
            kwargs = dict(
                model=self.model or "claude-sonnet-4-20250514",
                max_tokens=2048,
                messages=[{"role": "user", "content": user[:6000]}],
            )
            if system:
                kwargs["system"] = system[:4000]
            resp = client.messages.create(**kwargs)
            parts = []
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    parts.append(block.text)
            return "\n".join(parts)
        except Exception as e:
            return self._fallback(user, error=str(e))

    def _fallback(self, task: str, error: str = "") -> str:
        if error:
            print(f"  [fallback: {error[:80]}]")
        else:
            print("  [fallback: no ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN, using mock]")
        return f"mock result for task: {task[:80]}"
