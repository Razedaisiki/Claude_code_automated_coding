import re
from pathlib import Path
from typing import Optional

from agent_system.agents.models import AgentResult
from agent_system.context import ProjectContext, load_context
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


class TechLead:
    def __init__(self, root: Path, reasoning):
        self.root = root or Path.cwd()
        self.root = Path(self.root)
        self.reasoning = reasoning
        self.git = Git(self.root)

    def _invoke(self, system: str, user: str) -> str:
        try:
            text = self.reasoning.complete(system=system, user=user, max_tokens=2048, timeout=20)
            return text
        except Exception as e:
            raise RuntimeError(f"Parent API error: {e}") from e

    def _quick_baseline_check(self, task, baseline, diff: str):
        if not baseline or not baseline.files or not task.files:
            return None
        try:
            all_new = all(not snap.exists for snap in baseline.files.values())
            if not all_new:
                return None
            if not any(rel in diff for rel in task.files):
                return None
            if len(diff.strip()) < 50:
                return None
            return {"preservation_risk": False, "reason": "all task files were absent at baseline; new-file preservation concern does not apply"}
        except Exception:
            return None

    def _format_baseline(self, baseline) -> str:
        if not baseline:
            return "(no baseline captured)"
        lines = [f"HEAD: {baseline.commit_sha[:7] if baseline.commit_sha else '(none)'}"]
        for rel, snap in (baseline.files or {}).items():
            if snap.exists:
                lines.append(f"{rel}: exists (baseline)")
            else:
                lines.append(f"{rel}: did not exist at task baseline (new file is expected)")
        if not baseline.files:
            lines.append("(no task files tracked)")
        return "\n".join(lines)

    def _format_evidence(self, evidence) -> str:
        if not evidence or not evidence.events:
            return "(no tool evidence)"
        lines = []
        for ev in evidence.events:
            lines.append(f"{ev.tool} {ev.input} -> exit {ev.exit_code}: {ev.output}")
        return "\n".join(lines)

    def review(self, task, result: AgentResult, project_diff: str = "") -> AgentResult:
        if getattr(result, 'execution_status', 'COMPLETED') == "ERROR" or result.status == "FAILED":
            if getattr(result, 'execution_status', None) == "ERROR":
                return AgentResult(status="FAILED", message=f"task {task.id} runtime error: {result.message}", artifacts=result.artifacts, baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None), execution_status="ERROR", stop_reason=getattr(result, 'stop_reason', None))
            return AgentResult(status="FAILED", message=f"task {task.id} failed: {result.message}", artifacts=result.artifacts, baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))
        if result.status == "INCOMPLETE":
            return AgentResult(status="FAILED", message=f"task {task.id} incomplete: execution budget exhausted without producing expected changes", artifacts=result.artifacts, baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))
        if not result.message:
            return AgentResult(status="FAILED", message=f"task {task.id} produced empty result", artifacts=result.artifacts)
        if task.type == "verification":
            return AgentResult(status="SUCCESS", message=f"task {task.id} accepted (verification)", artifacts=result.artifacts)
        if task.type == "optional":
            return AgentResult(status="SUCCESS", message=f"task {task.id} accepted (optional)", artifacts=result.artifacts)
        if task.role != "code":
            return AgentResult(status="SUCCESS", message=f"task {task.id} accepted (non-code)", artifacts=result.artifacts)

        diff = (project_diff or "").strip()
        if not diff:
            sat = self._satisfaction_review(task, result)
            if sat is None:
                print(f"  Satisfaction review unavailable for {task.id} — failing closed")
                return AgentResult(status="FAILED", message=f"task {task.id} satisfaction review unavailable", artifacts=result.artifacts, baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))
            if sat.get("decision") == "ALREADY_SATISFIED":
                from agent_system.agents.models import TaskOutcome

                return AgentResult(status="SUCCESS", message=f"task {task.id} already satisfied: {sat.get('reason','')}", artifacts=result.artifacts, outcome=TaskOutcome(task_id=task.id, status="SATISFIED", decision="ALREADY_SATISFIED", reason=sat.get("reason",""), evidence=sat.get("evidence",[])), baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))
            if sat.get("decision") == "CHANGES_REQUIRED":
                return AgentResult(status="FAILED", message=f"task {task.id} not yet satisfied: {sat.get('reason','')}", artifacts=result.artifacts, baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))
            return AgentResult(status="FAILED", message=f"task {task.id} satisfaction review error", artifacts=result.artifacts, baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))
        ctx = load_context(self.root)
        baseline_obj = getattr(result, 'baseline', None)
        evidence_obj = getattr(result, 'evidence', None)
        baseline_text = self._format_baseline(baseline_obj)
        evidence_text = self._format_evidence(evidence_obj)
        quick = self._quick_baseline_check(task, baseline_obj, diff)
        if quick is not None:
            print(f"    baseline advisory: {quick['reason']}")
            baseline_text += f"\nAdvisory: {quick['reason']}"
        score = self._llm_review(task, result, diff, ctx, baseline_text=baseline_text, evidence_text=evidence_text)
        if score is None:
            print(f"  Review ERROR for {task.id}: reviewer unavailable or invalid response — failing closed")
            return AgentResult(status="FAILED", message=f"task {task.id} review error: reviewer unavailable", artifacts=result.artifacts, baseline=baseline_obj, evidence=evidence_obj)
        if not score.get("pass", True):
            return AgentResult(status="FAILED", message=f"task {task.id} review failed: {score.get('reason','')}", artifacts=result.artifacts, baseline=baseline_obj, evidence=evidence_obj)
        if diff.strip():
            print(f"    diff: {diff[:200]}")
        cm = self.commit_message(task, diff)
        from agent_system.agents.models import TaskOutcome as _TO2

        return AgentResult(status="SUCCESS", message=f"task {task.id} accepted", artifacts=result.artifacts, commit_message=cm, outcome=_TO2(task_id=task.id, status="CHANGED", decision="APPROVED"), baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))

    def _satisfaction_review(self, task, result):
        try:
            import json

            p = Path(__file__).parent.parent / "prompts" / "review" / "system.md"
            system = p.read_text(encoding="utf-8") if p.exists() else "You are a reviewer. Decide if the repository already satisfies the task. Return JSON: {\"decision\": \"ALREADY_SATISFIED\"|\"CHANGES_REQUIRED\", \"reason\": string}"
            ctx = load_context(self.root)
            baseline_text = self._format_baseline(getattr(result, 'baseline', None))
            evidence_text = self._format_evidence(getattr(result, 'evidence', None))
            candidates = list(dict.fromkeys(list(task.files or []) + list(getattr(result, "artifacts", None) or [])))
            repo_evidence = ""
            for rel in candidates:
                pp = self.root / rel
                if pp.is_file():
                    try:
                        repo_evidence += f"\n--- {rel} ---\n{pp.read_text(encoding='utf-8')}\n"
                    except Exception:
                        repo_evidence += f"\n--- {rel} --- (unreadable)\n"
                else:
                    repo_evidence += f"\n--- {rel} --- (not found)\n"
            try:
                r = self.git.shell.run("git ls-files")
                listing = r.stdout.strip() if r.returncode == 0 else ""
            except Exception:
                listing = ""
            repo_evidence += f"\n--- git ls-files ---\n{listing}\n" if listing else ""
            if task.acceptance:
                repo_evidence += "\nAcceptance:\n" + "\n".join(f"- {a}" for a in task.acceptance)
            user = f"Task: {task.description}\nBaseline:\n{baseline_text}\nRepo evidence:\n{repo_evidence}\nTool evidence:\n{evidence_text}\nPlan:\n{ctx.plan}\nResult:\n{result.message}\nDiff is empty — decide if the repository already satisfies all acceptance criteria."
            text = self.reasoning.complete(system=system, user=user, max_tokens=512, timeout=15)
            s = text.find("{")
            e = text.rfind("}") + 1
            if s >= 0 and e > s:
                data = json.loads(text[s:e])
                dec = data.get("decision", "")
                if dec in ("ALREADY_SATISFIED", "SATISFIED", "APPROVED", "NO_CHANGE"):
                    return {"decision": "ALREADY_SATISFIED", "reason": data.get("reason", text), "evidence": data.get("evidence", [])}
                if dec in ("CHANGES_REQUIRED", "REQUIRED"):
                    return {"decision": "CHANGES_REQUIRED", "reason": data.get("reason", text), "correction": data.get("correction", "")}
        except Exception:
            return None
        return None

    def _llm_review(self, task, result: AgentResult, diff: str, ctx: ProjectContext, baseline_text: str = "", evidence_text: str = ""):
        try:
            import json

            p = Path(__file__).parent.parent / "prompts" / "review" / "system.md"
            system = p.read_text(encoding="utf-8") if p.exists() else "You are a code reviewer. Reply JSON only: {\"decision\": \"APPROVED\"|\"CHANGES_REQUIRED\", \"reason\": string}"
            val = "\n".join(f"- {v}" for v in (task.validation or [])) or "(none)"
            acc = "\n".join(f"- {a}" for a in (task.acceptance or [])) or "(none)"
            baseline_text = baseline_text or self._format_baseline(getattr(result, 'baseline', None))
            evidence_text = evidence_text or self._format_evidence(getattr(result, 'evidence', None))
            user = f"Task: {task.description}\nAcceptance:\n{acc}\nValidation:\n{val}\nBaseline:\n{baseline_text}\nEvidence:\n{evidence_text}\nPlan:\n{ctx.plan}\nDiff:\n{diff}\nResult:\n{result.message}"
            text = self.reasoning.complete(system=system, user=user, max_tokens=512, timeout=15)
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                dec = str(data.get("decision", "")).upper()
                if dec in ("APPROVED", "ALREADY_SATISFIED", "SATISFIED"):
                    return {"pass": True, "reason": data.get("reason", ""), "decision": dec}
                if dec in ("CHANGES_REQUIRED", "REJECTED", "FAILED"):
                    return {"pass": False, "reason": data.get("reason", text), "correction": data.get("correction", ""), "decision": dec}
                if "pass" in data:
                    return data
        except Exception:
            return None
        return None

    def plan(self, task: str, ctx: ProjectContext) -> str:
        base = Path(__file__).parent.parent / "prompts"
        planning_path = base / "parent" / "planning.md"
        system = planning_path.read_text(encoding="utf-8") if planning_path.exists() else _load_prompt("parent")
        if (base / "common" / "engineering_rules.md").exists():
            system = system + "\n\n" + (base / "common" / "engineering_rules.md").read_text(encoding="utf-8")
        milestones_text = "\n\n".join(f"## {m.name}\n{m.content}" for m in ctx.milestones)
        user = f"Task:\n{task}\n\nRepo: {ctx.repository}\n\nCLAUDE.md:\n{ctx.instructions}\n\nMilestones:\n{milestones_text}"
        invoked = self._invoke(system, user)
        if invoked and not invoked.startswith("mock result"):
            s = invoked.find("{")
            e = invoked.rfind("}")
            if s >= 0 and e > s:
                try:
                    import json as _pj

                    from agent_system.plan_parser import is_valid_plan_data as _valid

                    cand = _pj.loads(invoked[s:e+1])
                    if _valid(cand):
                        return _pj.dumps(cand, ensure_ascii=False)
                except Exception:
                    pass
            print("  [plan: invalid structured response, preserving original task as fallback]")
        def _first_meaningful_line(t: str) -> str:
            for line in t.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                return s
            return "Implement feature"
        first_meaningful = _first_meaningful_line(task)
        import json as _jf

        FILE_HINT_RE = re.compile(r"(?:[\w.-]+/)*[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|yml|yaml|json|toml|md|sh)")
        _files_hint = list(dict.fromkeys(FILE_HINT_RE.findall(task)))

        def _extract_markdown_list_section(text: str, headings: set) -> list:
            norm_headings = {h.lower().rstrip(":").strip() for h in headings}
            all_known = {"acceptance", "acceptance criteria", "validation"}
            lines = text.splitlines()
            result: list[str] = []
            in_section = False
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                h = stripped.lstrip("#").strip().rstrip(":").lower()
                if h in norm_headings:
                    in_section = True
                    continue
                if in_section:
                    if re.match(r"^#{1,6}\s+", stripped):
                        break
                    nh = stripped.lstrip("#").strip().rstrip(":").lower()
                    if nh in norm_headings:
                        break
                    if nh in all_known and nh not in norm_headings:
                        break
                    m = re.match(r"^[-*]\s+(.*)", stripped)
                    if m:
                        item = m.group(1).strip()
                        if item:
                            result.append(item)
                        continue
                    m2 = re.match(r"^\d+[\.\)]\s+(.*)", stripped)
                    if m2:
                        item = m2.group(1).strip()
                        if item:
                            result.append(item)
                        continue
                    if re.match(r"^-{2,}$", stripped) or re.match(r"^={2,}$", stripped):
                        break
                    m3 = re.match(r"^[-*]\s*$", stripped)
                    if m3:
                        continue
            return result

        _acceptance = _extract_markdown_list_section(task, {"acceptance", "acceptance criteria"})
        _validation = _extract_markdown_list_section(task, {"validation"})

        acceptance = _acceptance if _acceptance else ["Satisfy all requirements described in the original executable task."]
        validation = _validation

        fallback = {
            "objective": first_meaningful,
            "analysis": "Structured planning output was unavailable. The original task is preserved as one complete executable delivery unit so requirements are not lost.",
            "tasks": [
                {
                    "id": "task001",
                    "role": "code",
                    "type": "implementation",
                    "description": task.strip(),
                    "acceptance": acceptance,
                    "validation": validation,
                    "files": _files_hint,
                }
            ],
            "risks": ["Planner structured output was unavailable; Runtime preserved the original task without semantic reduction."],
        }
        return _jf.dumps(fallback, ensure_ascii=False)

    def generate_commit_message(self, diff: str, hint: str = "") -> str:
        p = Path(__file__).parent.parent / "prompts" / "parent" / "commit_message.md"
        system = p.read_text(encoding="utf-8") if p.exists() else _load_prompt("parent/commit_message") or "Generate a commit message."
        user = f"Diff:\n{diff}\n\nHint:\n{hint}" if hint else f"Diff:\n{diff}"
        text = self._invoke(system, user)
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        if lines:
            msg = lines[0].strip('"').strip("'").strip()
            if msg:
                return msg
        return "chore: preserve existing changes"

    def commit_message(self, task, diff: str) -> str:
        return self.generate_commit_message(diff, hint=task.description)

    def _commit_message(self, task, diff: str) -> str:
        return self.commit_message(task, diff)

    def ci_review(self, ci_status: str, ci_logs: str = "", task=None, commit_sha: str = None) -> dict:
        p = Path(__file__).parent.parent / "prompts" / "parent" / "ci_review.md"
        sys_text = p.read_text(encoding="utf-8") if p.exists() else _load_prompt("parent/ci_review") or "You are the Tech Lead reviewing CI results."
        from agent_system.context import load_context

        ctx = load_context(self.root)
        from agent_system.runtime.git import Git

        git = Git(self.root)
        diff = git.commit_diff(commit_sha) if commit_sha else git.diff()
        if hasattr(task, 'description'):
            task_block = f"Executable task: {task.id}\nDescription: {task.description}\nAcceptance: {task.acceptance}\nValidation: {task.validation}\nFiles: {task.files}"
        else:
            task_block = str(task) if task else ""
        user = (
            f"Original TASK:\n{ctx.task}\n\n"
            f"Current executable task:\n{task_block}\n\n"
            f"Plan:\n{ctx.plan}\n\n"
            f"Commit: {commit_sha or ''}\n\n"
            f"Diff:\n{diff}\n\n"
            f"CI status: {ci_status}\n\nCI logs:\n{ci_logs}"
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
                    return {"decision": "APPROVED_WITH_NOTE" if decision == "NO_CODE_CHANGE" else decision, "reason": data.get("reason", text), "classification": data.get("classification", ""), "correction": data.get("correction", "")}
                if decision == "CHANGES_REQUIRED":
                    corr = data.get("correction", {})
                    if isinstance(corr, dict) and corr:
                        return {"decision": "CHANGES_REQUIRED", "reason": data.get("reason", text), "correction": corr, "classification": data.get("classification", "CHANGE_RELATED")}
                    return {"decision": "CHANGES_REQUIRED", "reason": data.get("reason", text), "correction": {}, "classification": "CHANGE_RELATED"}
        except Exception:
            pass
        if "APPROVE" in text.upper() or "CI_APPROVED" in text.upper():
            return {"decision": "APPROVED", "reason": text}
        low = text.lower()
        if "existing_project" in low or "infrastructure" in low:
            return {"decision": "APPROVED_WITH_NOTE", "reason": text, "classification": text}
        return {"decision": "CHANGES_REQUIRED", "reason": text, "correction": {"description": text}}

    def create_milestone(self, feedback: str = None) -> str:
        from agent_system.milestone.context import collect_context

        ctx = collect_context(self.root, feedback=feedback)
        p = Path(__file__).parent.parent / "prompts" / "parent" / "historian.md"
        system = p.read_text(encoding="utf-8") if p.exists() else _load_prompt("parent/historian") or "You are the Engineering Historian. Create a milestone document."
        prev_text = "\n\n---\n\n".join(ctx.previous_milestones[-2:]) if ctx.previous_milestones else "(none)"
        user = (
            f"Task:\n{ctx.task}\n\n"
            f"Plan:\n{ctx.plan}\n\n"
            f"Diff:\n{ctx.diff}\n\n"
            f"Git log:\n{ctx.git_log}\n\n"
            f"Repo state:\n{ctx.repo_state}\n\n"
            f"Previous milestones (last 2):\n{prev_text}\n\n"
            f"Human feedback:\n{ctx.human_feedback or '(none)'}"
        )
        content = self._invoke(system, user)
        milestones_dir = self.root / ".agent" / "milestones"
        milestones_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(milestones_dir.glob("*.md"))
        next_id = len(existing) + 1
        fname = f"{next_id:03d}.md"
        out = milestones_dir / fname
        out.write_text(content if content.lstrip().startswith("#") else f"# Milestone {next_id:03d}\n\n{content}", encoding="utf-8")
        return str(out)
