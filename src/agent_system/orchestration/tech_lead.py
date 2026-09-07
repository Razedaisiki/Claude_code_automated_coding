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

    def _invoke(self, system: str, user: str, *, max_tokens: int = 2048, timeout: int = 1200) -> str:
        try:
            text = self.reasoning.complete(system=system, user=user, max_tokens=max_tokens, timeout=timeout)
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
        # Legacy wrapper: build minimal review package and delegate to review_package
        try:
            fake_pkg = {"reviewed_tree_sha": self.git.head_tree_sha(), "base_commit_sha": self.git.head_sha(), "base_tree_sha": self.git.head_tree_sha(), "changed_files": [], "project_diff": project_diff or "", "relevant_files": [], "candidate": {"result_status": getattr(result, 'status', 'SUCCESS'), "result_message": getattr(result, 'message', ''), "result_artifacts": list(getattr(result, 'artifacts', None) or []), "execution_evidence": getattr(result, 'evidence', None)}, "validation": None, "task_baseline": None}
            return self.review_package(task, fake_pkg, None)
        except Exception:
            pass
        # fallback to legacy minimal
        return self.review_package(task, {"project_diff": project_diff or "", "relevant_files": [], "candidate": {}, "validation": None, "reviewed_tree_sha": "", "base_commit_sha": "", "base_tree_sha": "", "changed_files": [], "task_baseline": None}, None)

    def review_package(self, task, review_package: dict, project_context=None) -> AgentResult:
        pkg = review_package or {}
        candidate = pkg.get("candidate") or {}
        validation = pkg.get("validation")
        # Error states from candidate
        rs = candidate.get("result_status", "SUCCESS")
        if candidate.get("execution_status") == "ERROR" or rs == "FAILED":
            if candidate.get("execution_status") == "ERROR":
                return AgentResult(status="FAILED", message=f"task {task.id} runtime error: {candidate.get('result_message','')}", artifacts=candidate.get("result_artifacts", []))
            return AgentResult(status="FAILED", message=f"task {task.id} failed: {candidate.get('result_message','')}", artifacts=candidate.get("result_artifacts", []))
        if rs == "INCOMPLETE":
            return AgentResult(status="FAILED", message=f"task {task.id} incomplete: execution budget exhausted", artifacts=candidate.get("result_artifacts", []))
        if not candidate.get("result_message"):
            return AgentResult(status="FAILED", message=f"task {task.id} produced empty result", artifacts=candidate.get("result_artifacts", []))
        # Empty diff: satisfaction via unified package
        diff = (pkg.get("project_diff") or "").strip()
        if not diff and not pkg.get("changed_files"):
            sat = self._satisfaction_review_package(task, pkg, project_context)
            if sat is None:
                print(f"  Satisfaction review unavailable for {task.id} — failing closed")
                return AgentResult(status="FAILED", message=f"task {task.id} satisfaction review unavailable", artifacts=candidate.get("result_artifacts", []))
            if sat.get("decision") == "ALREADY_SATISFIED":
                from agent_system.agents.models import TaskOutcome
                return AgentResult(status="SUCCESS", message=f"task {task.id} already satisfied: {sat.get('reason','')}", artifacts=candidate.get("result_artifacts", []), outcome=TaskOutcome(task_id=task.id, status="SATISFIED", decision="ALREADY_SATISFIED", reason=sat.get("reason",""), evidence=sat.get("evidence",[])))
            if sat.get("decision") == "CHANGES_REQUIRED":
                return AgentResult(status="FAILED", message=f"task {task.id} not yet satisfied: {sat.get('reason','')}", artifacts=candidate.get("result_artifacts", []))
            return AgentResult(status="FAILED", message=f"task {task.id} satisfaction review error", artifacts=candidate.get("result_artifacts", []))
        score = self._llm_review_package(task, pkg, project_context)
        if score is None:
            print(f"  Review ERROR for {task.id}: reviewer unavailable or invalid response — failing closed")
            return AgentResult(status="FAILED", message=f"task {task.id} review error: reviewer unavailable", artifacts=candidate.get("result_artifacts", []))
        if not score.get("pass", True):
            return AgentResult(status="FAILED", message=f"task {task.id} review failed: {score.get('reason','')}", artifacts=candidate.get("result_artifacts", []))
        if diff:
            print(f"    diff: {diff[:200]}")
        cm = self.commit_message(task, diff)
        from agent_system.agents.models import TaskOutcome as _TO2
        return AgentResult(status="SUCCESS", message=f"task {task.id} accepted", artifacts=candidate.get("result_artifacts", []), commit_message=cm, outcome=_TO2(task_id=task.id, status="CHANGED", decision="APPROVED"))

    def _satisfaction_review(self, task, result):
        try:
            import json

            p = Path(__file__).parent.parent / "prompts" / "review" / "system.md"
            system = p.read_text(encoding="utf-8") if p.exists() else "You are a reviewer. Decide if the repository already satisfies the task. Return JSON: {\"decision\": \"ALREADY_SATISFIED\"|\"CHANGES_REQUIRED\", \"reason\": string}"
            ctx = load_context(self.root)
            baseline_text = self._format_baseline(getattr(result, 'baseline', None))
            evidence_text = self._format_evidence(getattr(result, 'evidence', None))
            from agent_system.runtime.path_safety import safe_read_text
            candidates = list(dict.fromkeys(list(task.files or []) + list(getattr(result, "artifacts", None) or [])))
            repo_evidence = ""
            for rel in candidates:
                text, err = safe_read_text(self.root, rel)
                if err:
                    if "symlink" in err:
                        try:
                            target = str((self.root / rel).readlink()) if (self.root / rel).is_symlink() else ""
                            repo_evidence += f"\n--- {rel} --- (symlink -> {target})\n"
                        except Exception:
                            repo_evidence += f"\n--- {rel} --- (symlink)\n"
                    elif "unsafe" in err or "escapes" in err:
                        repo_evidence += f"\n--- {rel} --- (unsafe path rejected)\n"
                    else:
                        pp = self.root / rel
                        if not pp.exists():
                            repo_evidence += f"\n--- {rel} --- (not found)\n"
                        else:
                            repo_evidence += f"\n--- {rel} --- (unreadable: {err})\n"
                    continue
                repo_evidence += f"\n--- {rel} ---\n{text}\n" if text else f"\n--- {rel} ---\n(empty)\n"
            try:
                r = self.git.shell.run("git ls-files")
                listing = r.stdout.strip() if r.returncode == 0 else ""
            except Exception:
                listing = ""
            repo_evidence += f"\n--- git ls-files ---\n{listing}\n" if listing else ""
            if task.acceptance:
                repo_evidence += "\nAcceptance:\n" + "\n".join(f"- {a}" for a in task.acceptance)
            user = f"Task: {task.description}\nBaseline:\n{baseline_text}\nRepo evidence:\n{repo_evidence}\nTool evidence:\n{evidence_text}\nPlan:\n{ctx.plan}\nResult:\n{result.message}\nDiff is empty — decide if the repository already satisfies all acceptance criteria."
            text = self.reasoning.complete(system=system, user=user, max_tokens=512, timeout=1200)
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
            text = self.reasoning.complete(system=system, user=user, max_tokens=512, timeout=1200)
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

    def _format_validation_evidence(self, validation) -> str:
        if not validation:
            return "(no validation)"
        lines = [f"Overall: {validation.get('status','')}", f"Tree: {validation.get('tree_sha','') or validation.get('tree_sha','')}" ]
        for i, c in enumerate(validation.get("checks", []) or []):
            lines.append(f"{i+1}. {c.get('instruction','')}")
            lines.append(f"   {c.get('status','')} - {c.get('evidence','')[:400]}")
        if validation.get("summary"):
            lines.append(f"Summary: {validation.get('summary','')[:400]}")
        return "\n".join(lines)

    def _format_relevant_files(self, pkg) -> str:
        rf = pkg.get("relevant_files") or []
        if not rf:
            return "(no relevant files)"
        parts = []
        for fe in rf:
            path = fe.get("path","")
            kind = fe.get("kind","")
            if kind == "symlink":
                parts.append(f"--- {path} --- (symlink -> {fe.get('symlink_target','')})")
            elif kind == "missing":
                parts.append(f"--- {path} --- (missing)")
            elif fe.get("binary"):
                parts.append(f"--- {path} --- (binary {fe.get('size',0)} bytes, blob {fe.get('blob_sha','')[:7]})")
            else:
                content = fe.get("content","") or ""
                parts.append(f"--- {path} ---\n{content}")
        return "\n\n".join(parts)

    def _satisfaction_review_package(self, task, pkg, project_context=None):
        try:
            import json
            p = __import__("pathlib").Path(__file__).parent.parent / "prompts" / "review" / "system.md"
            system = p.read_text(encoding="utf-8") if p.exists() else "You are a reviewer."
            ctx = project_context or __import__("agent_system.context", fromlist=["load_context"]).load_context(self.root)
            candidate = pkg.get("candidate") or {}
            validation = pkg.get("validation")
            from agent_system.agents.models import execution_evidence_from_dict
            # need baseline from pkg
            relevant = self._format_relevant_files(pkg)
            val_text = self._format_validation_evidence(validation)
            # code agent evidence
            ev = candidate.get("execution_evidence")
            evidence_text = ""
            if isinstance(ev, dict):
                from agent_system.agents.models import execution_evidence_from_dict as _from
                ee = _from(ev)
                evidence_text = self._format_evidence(ee) if ee else "(no evidence)"
            else:
                evidence_text = "(no evidence)"
            baseline_text = "(no baseline)"
            # Add review invariant rules
            system_extra = "\n\nThe Git diff is only the delta between base tree and reviewed candidate tree. It is NOT a complete representation of every relevant file. The Relevant Files section is authoritative.\nDo not infer missing code merely because it does not appear in the diff."
            system = system + system_extra
            user = f"Reviewed Tree SHA: {pkg.get('reviewed_tree_sha','')}\nBase Commit SHA: {pkg.get('base_commit_sha','')}\n\nTask: {task.description}\nAcceptance:\n" + "\n".join(f"- {a}" for a in (task.acceptance or [])) + f"\n\nRelevant Files at Reviewed Tree:\n{relevant}\n\nRuntime Validation:\n{val_text}\n\nCodeAgent Evidence:\n{evidence_text}\n\nChanges from Base:\n{(pkg.get('project_diff','') or '').strip()[:4000] or '(empty diff)'}\n\nDiff is empty — decide if the repository already satisfies all acceptance criteria."
            text = self._invoke(system, user)
            s = text.find("{")
            e = text.rfind("}") + 1
            if s >= 0 and e > s:
                data = json.loads(text[s:e])
                dec = data.get("decision", "")
                if dec in ("ALREADY_SATISFIED", "SATISFIED", "APPROVED", "NO_CHANGE"):
                    return {"decision": "ALREADY_SATISFIED", "reason": data.get("reason", text), "evidence": data.get("evidence", [])}
                if dec in ("CHANGES_REQUIRED", "REQUIRED"):
                    return {"decision": "CHANGES_REQUIRED", "reason": data.get("reason", text)}
        except Exception:
            return None
        return None

    def _llm_review_package(self, task, pkg, project_context=None):
        try:
            import json
            p = __import__("pathlib").Path(__file__).parent.parent / "prompts" / "review" / "system.md"
            system = p.read_text(encoding="utf-8") if p.exists() else "You are a code reviewer."
            ctx = project_context or __import__("agent_system.context", fromlist=["load_context"]).load_context(self.root)
            candidate = pkg.get("candidate") or {}
            validation = pkg.get("validation")
            relevant = self._format_relevant_files(pkg)
            val_text = self._format_validation_evidence(validation)
            # code evidence
            ev = candidate.get("execution_evidence")
            evidence_text = "(no tool evidence)"
            if isinstance(ev, dict):
                from agent_system.agents.models import execution_evidence_from_dict as _from2
                ee = _from2(ev)
                evidence_text = self._format_evidence(ee) if ee and ee.events else "(no tool evidence)"
            # baseline
            tb = pkg.get("task_baseline")
            if isinstance(tb, dict):
                lines = [f"HEAD: {tb.get('commit_sha','')[:7]}"]
                for rel, snap in (tb.get("files") or {}).items():
                    if isinstance(snap, dict) and snap.get("exists"):
                        lines.append(f"{rel}: exists (baseline)")
                    else:
                        lines.append(f"{rel}: did not exist")
                baseline_text = "\n".join(lines)
            else:
                baseline_text = "(no baseline)"
            # Evidence authority ordering in prompt
            system_extra = "\n\nThe Git diff is only the delta between the base tree and the reviewed candidate tree. It is NOT a complete representation of every relevant file. Do not infer that unchanged code is missing merely because it does not appear in the diff. The Relevant Files at Reviewed Tree section is the authoritative representation of current relevant file contents. Use: Relevant Files to understand the complete candidate state; Diff to understand what changed; Validation Evidence to determine what was independently verified; Code Agent Evidence only as supporting execution evidence.\n\nEvidence authority: 1. Reviewed Git tree contents 2. Runtime Validation evidence 3. Git diff 4. Task baseline 5. CodeAgent tool evidence 6. CodeAgent completion summary\n\nFor correction tasks, unchanged implementation from the base commit may not appear in the correction diff. Use the full Relevant Files section and Runtime Validation evidence when checking original behavior preservation. Do not require previously committed unchanged code to be reintroduced into the correction diff."
            system = system + system_extra
            acc = "\n".join(f"- {a}" for a in (task.acceptance or [])) or "(none)"
            val_goals = "\n".join(f"- {v}" for v in (task.validation or [])) or "(none)"
            user = f"Reviewed Tree SHA: {pkg.get('reviewed_tree_sha','')}\nBase Commit SHA: {pkg.get('base_commit_sha','')}\n\nEverything in this package refers to this exact reviewed tree.\n\nTask: {task.description}\nAcceptance:\n{acc}\nValidation Goals:\n{val_goals}\n\nRelevant Files at Reviewed Tree:\n{relevant}\n\nRuntime Validation:\n{val_text}\n\nCodeAgent Execution Evidence:\n{evidence_text}\n\nTask Baseline:\n{baseline_text}\n\nChanges from Base Tree to Reviewed Tree:\n{(pkg.get('project_diff','') or '(empty)').strip()[:6000]}\n\nFrozen Plan Context:\n{(ctx.plan or '(none)')[:1000]}\n\nCodeAgent Completion Summary:\n{candidate.get('result_message','')[:1000]}"
            text = self._invoke(system, user)
            s = text.find("{")
            e = text.rfind("}") + 1
            if s >= 0 and e > s:
                data = json.loads(text[s:e])
                dec = str(data.get("decision", "")).upper()
                if dec in ("APPROVED", "ALREADY_SATISFIED", "SATISFIED"):
                    return {"pass": True, "reason": data.get("reason", ""), "decision": dec}
                if dec in ("CHANGES_REQUIRED", "REJECTED", "FAILED"):
                    return {"pass": False, "reason": data.get("reason", text), "decision": dec}
                if "pass" in data:
                    return data
        except Exception:
            return None
        return None

    def plan(self, task: str, ctx: ProjectContext) -> str:
        from agent_system.planning.config import get_planner_config
        from agent_system.planning.planner import StructuredPlanner, PlanningError

        config = get_planner_config()
        planner = StructuredPlanner(root=self.root, reasoning=self.reasoning, config=config)
        try:
            return planner.create_plan(task, ctx)
        except PlanningError as e:
            if config.fallback_mode == "single_task":
                print(f"WARNING: structured planning failed; explicit single-task fallback enabled — {e}")
                return self._single_task_fallback(task)
            raise
        except Exception as e:
            if config.fallback_mode == "single_task":
                print(f"WARNING: structured planning failed; explicit single-task fallback enabled — {e}")
                return self._single_task_fallback(task)
            raise PlanningError(f"planning failed: {e}") from e

    def _single_task_fallback(self, task: str) -> str:
        def _first_meaningful_line(t: str) -> str:
            for line in t.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if re.fullmatch(r"[-=_*]{3,}", s):
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
        fallback_data = {
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
            "planner": {"version": 2, "strategy": "fallback-single-task", "task_count": 1, "fallback_used": True},
        }
        return _jf.dumps(fallback_data, ensure_ascii=False)

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
        from agent_system.plan_parser import render_plan_context

        ctx = load_context(self.root)
        from agent_system.runtime.git import Git

        git = Git(self.root)
        diff = git.commit_diff(commit_sha) if commit_sha else git.diff()
        # Use semantic plan context instead of raw plan dump
        try:
            plan_ctx = render_plan_context(ctx.plan_data, current_task_id=getattr(task, 'id', None) if task else None, max_chars=4000)
        except Exception:
            plan_ctx = (ctx.plan or "(none)")[:4000]
        if hasattr(task, 'description'):
            task_block = f"Executable task: {task.id}\nDescription: {task.description}\nAcceptance: {task.acceptance}\nValidation: {task.validation}\nFiles: {task.files}"
        else:
            task_block = str(task) if task else ""
        user = (
            f"Original TASK:\n{ctx.task}\n\n"
            f"Current executable task:\n{task_block}\n\n"
            f"Plan:\n{plan_ctx}\n\n"
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
