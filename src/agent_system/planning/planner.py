from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

from agent_system.context import ProjectContext
from agent_system.plan_parser import extract_json_object, validate_plan_data
from agent_system.planning.config import PlannerConfig, get_planner_config
from agent_system.planning.repository_map import build_repository_map


class PlanningError(RuntimeError):
    pass


def _load_prompt(name: str) -> str:
    base = Path(__file__).parent.parent / "prompts" / "parent"
    for fname in [f"{name}.md", f"{name}", name]:
        p = base / fname
        if p.exists():
            return p.read_text(encoding="utf-8")
    # fallback to planning.md
    p = base / "planning.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def _write_debug(root: Path, rel: str, content: str):
    try:
        p = Path(root or Path.cwd()) / ".agent" / "debug" / "planner" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except Exception:
        pass


class StructuredPlanner:
    def __init__(self, root: Path = None, reasoning=None, config: Optional[PlannerConfig] = None):
        self.root = Path(root or Path.cwd()).resolve()
        self.reasoning = reasoning
        self.config = config or get_planner_config()

    def _invoke(self, system: str, user: str) -> str:
        return self.reasoning.complete(system=system, user=user, max_tokens=self.config.max_tokens, timeout=1200)

    def create_plan(self, task: str, ctx: ProjectContext) -> str:
        # Use repository_map instead of raw .git/HEAD
        repo_map = build_repository_map(self.root)
        # Stage 1: skeleton
        skeleton = self._stage_skeleton(task, ctx, repo_map)
        # Stage 2: refinement
        skeleton = self._stage_refine(skeleton, task, ctx, repo_map)
        # Assign IDs deterministically
        tasks_skeleton = skeleton.get("tasks", []) if isinstance(skeleton, dict) else []
        for idx, t in enumerate(tasks_skeleton):
            if isinstance(t, dict):
                t["id"] = f"task{idx+1:03d}"
        objective = str(skeleton.get("objective", "")).strip() if isinstance(skeleton, dict) else ""
        analysis = str(skeleton.get("analysis", "")).strip() if isinstance(skeleton, dict) else ""
        # Stage 3: batched enrichment
        enriched_tasks = self._stage_enrich(tasks_skeleton, task, ctx, repo_map)
        # Build final plan
        final_tasks = enriched_tasks
        # Ensure IDs stable
        for idx, t in enumerate(final_tasks):
            if isinstance(t, dict):
                t["id"] = f"task{idx+1:03d}"
        plan = {
            "objective": objective or task.strip().splitlines()[0][:200],
            "analysis": analysis or f"Decomposed into {len(final_tasks)} focused engineering tasks.",
            "tasks": final_tasks,
            "risks": [],
            "planner": {
                "version": 2,
                "strategy": "staged-max-safe-decomposition",
                "task_count": len(final_tasks),
                "split_rounds": self._last_split_rounds,
                "fallback_used": False,
            },
        }
        # Stage 4: validation/repair
        plan = self._validate_and_repair(plan)
        # Store debug artifact final
        _write_debug(self.root, "final.json", json.dumps(plan, indent=2, ensure_ascii=False))
        return json.dumps(plan, ensure_ascii=False)

    def _stage_skeleton(self, task: str, ctx: ProjectContext, repo_map: str) -> dict:
        system = _load_prompt("planning_skeleton")
        if not system:
            system = "Create a compact task skeleton."
        common = ""
        try:
            p = Path(__file__).parent.parent / "prompts" / "common" / "engineering_rules.md"
            if p.exists():
                common = p.read_text(encoding="utf-8")
                system = system + "\n\n" + common
        except Exception:
            pass
        milestones_text = "\n\n".join(f"## {m.name}\n{m.content}" for m in (ctx.milestones or []))
        user = f"Original Task:\n{task}\n\nRepository Map:\n{repo_map}\n\nCLAUDE.md:\n{ctx.instructions or '(none)'}\n\nMilestones:\n{milestones_text or '(none)'}"
        raw = self._invoke(system, user)
        _write_debug(self.root, "skeleton.raw.txt", raw)
        obj = extract_json_object(raw)
        _write_debug(self.root, "skeleton.json", json.dumps(obj, indent=2, ensure_ascii=False) if obj else raw)
        if obj is None or not isinstance(obj.get("tasks"), list) or not obj["tasks"]:
            # Attempt repair for skeleton
            obj = self._repair_skeleton(raw, task, ctx, repo_map)
        if obj is None or not isinstance(obj.get("tasks"), list) or not obj["tasks"]:
            raise PlanningError(f"planning failed: skeleton generation failed — no tasks produced. Raw: {raw[:500]}")
        # Basic shape check for skeleton entries (title/scope)
        skeleton_tasks = []
        for t in obj.get("tasks", []):
            if not isinstance(t, dict):
                continue
            title = str(t.get("title") or t.get("description") or t.get("name") or "").strip()
            scope = str(t.get("scope") or t.get("analysis") or t.get("summary") or "").strip()
            if not title:
                continue
            skeleton_tasks.append({"title": title, "scope": scope or title})
        if len(skeleton_tasks) > self.config.max_tasks:
            skeleton_tasks = skeleton_tasks[: self.config.max_tasks]
        print(f"Planner: skeleton produced {len(skeleton_tasks)} tasks")
        return {"objective": str(obj.get("objective", "")).strip() or task.strip().splitlines()[0][:200], "analysis": str(obj.get("analysis", "")).strip(), "tasks": skeleton_tasks}

    def _repair_skeleton(self, raw: str, task: str, ctx: ProjectContext, repo_map: str) -> Optional[dict]:
        for attempt in range(1, self.config.repair_attempts + 1):
            errors = ["Skeleton JSON invalid or missing tasks"]
            schema = '{"objective":"...","tasks":[{"title":"...","scope":"..."}]}'
            repair_system = _load_prompt("planning_repair")
            repair_user = repair_system.replace("{{errors}}", "\n".join(f"- {e}" for e in errors)).replace("{{raw}}", raw[:4000]).replace("{{schema}}", schema)
            try:
                repaired = self._invoke(repair_system, repair_user)
                _write_debug(self.root, f"repair-skeleton-{attempt}.raw.txt", repaired)
                obj = extract_json_object(repaired)
                if obj is not None and isinstance(obj.get("tasks"), list) and obj["tasks"]:
                    _write_debug(self.root, f"repair-skeleton-{attempt}.json", json.dumps(obj, indent=2, ensure_ascii=False))
                    print(f"Planner repair attempt {attempt}/{self.config.repair_attempts} for skeleton")
                    return obj
            except Exception as e:
                _write_debug(self.root, f"repair-skeleton-{attempt}.error.txt", str(e))
        return None

    def _stage_refine(self, skeleton: dict, task: str, ctx: ProjectContext, repo_map: str) -> dict:
        tasks = list(skeleton.get("tasks", []) if isinstance(skeleton, dict) else [])
        objective = str(skeleton.get("objective", "")).strip()
        analysis = str(skeleton.get("analysis", "")).strip()
        system = _load_prompt("planning_refine")
        self._last_split_rounds = 0
        for rnd in range(1, self.config.split_rounds + 1):
            if len(tasks) >= self.config.max_tasks:
                break
            compact = json.dumps({"objective": objective, "tasks": tasks}, ensure_ascii=False)
            user = f"Original Task:\n{task}\n\nCurrent skeleton ({len(tasks)} tasks):\n{compact}\n\nRepository Map:\n{repo_map[:2000]}"
            raw = self._invoke(system, user)
            _write_debug(self.root, f"refine-{rnd:02d}.raw.txt", raw)
            obj = extract_json_object(raw)
            if obj is None:
                _write_debug(self.root, f"refine-{rnd:02d}.error.txt", "invalid JSON")
                print(f"Planner: refinement round {rnd} — invalid JSON, stopping")
                break
            _write_debug(self.root, f"refine-{rnd:02d}.json", json.dumps(obj, indent=2, ensure_ascii=False))
            changed = bool(obj.get("changed", False))
            new_tasks_raw = obj.get("tasks", [])
            if not isinstance(new_tasks_raw, list) or not new_tasks_raw:
                print(f"Planner: refinement round {rnd} — no tasks, stopping")
                break
            # Normalize
            new_tasks = []
            for t in new_tasks_raw:
                if not isinstance(t, dict):
                    continue
                title = str(t.get("title") or t.get("description") or t.get("name") or "").strip()
                scope = str(t.get("scope") or t.get("analysis") or "").strip()
                if not title:
                    continue
                new_tasks.append({"title": title, "scope": scope or title})
            if len(new_tasks) > self.config.max_tasks:
                new_tasks = new_tasks[: self.config.max_tasks]
            if not changed or len(new_tasks) == len(tasks):
                # Check if actually changed content
                if not changed:
                    print(f"Planner: refinement round {rnd} -> {len(tasks)} tasks (converged)")
                    break
                # Changed true but same length — still consider
            tasks = new_tasks
            self._last_split_rounds = rnd
            print(f"Planner: refinement round {rnd} -> {len(tasks)} tasks")
            if not changed:
                break
        skeleton["tasks"] = tasks
        return skeleton

    def _stage_enrich(self, skeleton_tasks: List[dict], task: str, ctx: ProjectContext, repo_map: str) -> List[dict]:
        system = _load_prompt("planning_enrich")
        batch_size = max(1, self.config.enrich_batch_size)
        enriched: List[dict] = []
        total_batches = (len(skeleton_tasks) + batch_size - 1) // batch_size
        for batch_idx in range(total_batches):
            start = batch_idx * batch_size
            batch = skeleton_tasks[start : start + batch_size]
            batch_ids = [t.get("id") for t in batch]
            skeleton_json = json.dumps({"tasks": batch}, ensure_ascii=False)
            user = f"Original Task:\n{task}\n\nRepository Map:\n{repo_map[:2000]}\n\nEnrich batch {batch_idx+1}/{total_batches} ({batch_ids[0] if batch_ids else ''}-{batch_ids[-1] if batch_ids else ''}):\n{skeleton_json}\n\nReturn enriched tasks for this batch only."
            print(f"Planner: enriching tasks {batch_ids[0] if batch_ids else ''}-{batch_ids[-1] if batch_ids else ''} (batch {batch_idx+1}/{total_batches})")
            raw = self._invoke(system, user)
            _write_debug(self.root, f"enrich-{start+1:03d}-{start+len(batch):03d}.raw.txt", raw)
            obj = extract_json_object(raw)
            if obj is None or not isinstance(obj.get("tasks"), list):
                # Try repair for this batch
                obj = self._repair_batch(raw, batch, batch_idx)
                if obj is None:
                    raise PlanningError(f"planning failed: enrichment batch {batch_idx+1} returned invalid JSON. Raw: {raw[:600]}")
            tasks_raw = obj.get("tasks", [])
            # Verify IDs preserved
            expected_ids = set(batch_ids)
            got_ids = set(str(t.get("id", "")).strip() if isinstance(t, dict) else "" for t in tasks_raw)
            if expected_ids != got_ids:
                # Attempt repair preserving IDs
                repaired = self._repair_batch_ids(raw, batch, batch_idx, tasks_raw)
                if repaired is not None:
                    tasks_raw = repaired
                else:
                    raise PlanningError(f"planning failed: enrichment batch {batch_idx+1} ID mismatch — expected {sorted(expected_ids)} got {sorted(got_ids)}")
            _write_debug(self.root, f"enrich-{start+1:03d}-{start+len(batch):03d}.json", json.dumps(obj, indent=2, ensure_ascii=False))
            # Normalize and validate each enriched task minimally
            for t in tasks_raw:
                if not isinstance(t, dict):
                    continue
                # Ensure required fields
                if not isinstance(t.get("description"), str) or not t.get("description", "").strip():
                    # fallback to title/scope
                    title = next((b.get("title", "") for b in batch if b.get("id") == t.get("id")), t.get("id", ""))
                    t["description"] = title or t.get("id", "")
                if not isinstance(t.get("acceptance"), list):
                    t["acceptance"] = [str(t.get("acceptance", "")).strip()] if t.get("acceptance") else ["Task completed as described."]
                if not isinstance(t.get("validation"), list):
                    t["validation"] = [str(t.get("validation", "")).strip()] if t.get("validation") else ["Verify the task requirements are satisfied."]
                if not isinstance(t.get("files"), list):
                    t["files"] = []
                enriched.extend(tasks_raw if len(enriched) == start else [])
                break
            # Correct extension (avoid duplicate due to logic above)
            if len(enriched) == start:
                enriched.extend(tasks_raw)
        print(f"Planner: final plan contains {len(enriched)} tasks")
        return enriched

    def _repair_batch(self, raw: str, batch: List[dict], batch_idx: int) -> Optional[dict]:
        for attempt in range(1, self.config.repair_attempts + 1):
            schema = '{"tasks":[{"id":"task001","role":"code","type":"implementation","description":"...","acceptance":["..."],"validation":["..."],"files":[]}]}'
            errors = [f"Batch {batch_idx+1} invalid JSON or missing tasks"]
            repair_system = _load_prompt("planning_repair")
            repair_user = repair_system.replace("{{errors}}", "\n".join(f"- {e}" for e in errors)).replace("{{raw}}", raw[:4000]).replace("{{schema}}", schema)
            try:
                repaired = self._invoke(repair_system, repair_user)
                _write_debug(self.root, f"repair-enrich-{batch_idx+1}-{attempt}.raw.txt", repaired)
                obj = extract_json_object(repaired)
                if obj is not None and isinstance(obj.get("tasks"), list) and obj["tasks"]:
                    print(f"Planner repair attempt {attempt}/{self.config.repair_attempts} for enrich batch {batch_idx+1}")
                    return obj
            except Exception as e:
                _write_debug(self.root, f"repair-enrich-{batch_idx+1}-{attempt}.error.txt", str(e))
        return None

    def _repair_batch_ids(self, raw: str, batch: List[dict], batch_idx: int, tasks_raw: List[dict]) -> Optional[List[dict]]:
        expected_ids = [b.get("id") for b in batch]
        for attempt in range(1, self.config.repair_attempts + 1):
            errors = [f"Expected IDs {expected_ids} but got {[str(t.get('id','')) for t in tasks_raw if isinstance(t, dict)]}"]
            schema = f'{{"tasks":[{{"id":"{expected_ids[0] if expected_ids else "task001"}","role":"code","type":"implementation","description":"...","acceptance":["..."],"validation":["..."],"files":[]}}]}}'
            repair_system = _load_prompt("planning_repair")
            repair_user = repair_system.replace("{{errors}}", "\n".join(f"- {e}" for e in errors)).replace("{{raw}}", raw[:4000]).replace("{{schema}}", schema)
            try:
                repaired = self._invoke(repair_system, repair_user)
                _write_debug(self.root, f"repair-ids-{batch_idx+1}-{attempt}.raw.txt", repaired)
                obj = extract_json_object(repaired)
                if obj is not None and isinstance(obj.get("tasks"), list):
                    got = [str(t.get("id","")).strip() for t in obj["tasks"] if isinstance(t, dict)]
                    if set(got) == set(expected_ids):
                        return obj["tasks"]
            except Exception:
                pass
            break
        return None

    def _validate_and_repair(self, plan: dict) -> dict:
        errors = validate_plan_data(plan, max_tasks=self.config.max_tasks)
        if not errors:
            return plan
        _write_debug(self.root, "validation-errors.json", json.dumps(errors, indent=2, ensure_ascii=False))
        print("Planner validation failed:")
        for e in errors:
            print(f"  - {e}")
        for attempt in range(1, self.config.repair_attempts + 1):
            print(f"Planner repair attempt {attempt}/{self.config.repair_attempts}")
            repair_system = _load_prompt("planning_repair")
            schema = '{"objective":"...","analysis":"...","tasks":[{"id":"task001","role":"code","type":"implementation","description":"...","acceptance":["..."],"validation":["..."],"files":[]}],"risks":[]}'
            repair_user = repair_system.replace("{{errors}}", "\n".join(f"- {e}" for e in errors)).replace("{{raw}}", json.dumps(plan, indent=2, ensure_ascii=False)[:6000]).replace("{{schema}}", schema)
            raw = self._invoke(repair_system, repair_user)
            _write_debug(self.root, f"repair-final-{attempt}.raw.txt", raw)
            obj = extract_json_object(raw)
            if obj is None:
                continue
            # Preserve task count if possible: ensure not collapsing
            orig_count = len(plan.get("tasks", []))
            new_count = len(obj.get("tasks", [])) if isinstance(obj.get("tasks"), list) else 0
            if new_count < orig_count and new_count == 1 and orig_count > 3:
                _write_debug(self.root, f"repair-final-{attempt}.collapsed.txt", f"rejected collapse {orig_count}->{new_count}")
                continue
            errors2 = validate_plan_data(obj, max_tasks=self.config.max_tasks)
            _write_debug(self.root, f"repair-final-{attempt}.json", json.dumps(obj, indent=2, ensure_ascii=False))
            if not errors2:
                print(f"Planner repair succeeded ({new_count} tasks)")
                return obj
            errors = errors2
        # Still invalid
        if self.config.fallback_mode == "single_task":
            print("WARNING: structured planning failed; explicit single-task fallback enabled")
            # Build fallback from original task
            # This should not be reached in normal error mode
            fallback_plan = self._build_single_task_fallback(plan)
            fallback_plan["planner"] = {"version": 2, "strategy": "fallback-single-task", "task_count": 1, "fallback_used": True}
            return fallback_plan
        raise PlanningError("planning failed: " + "; ".join(errors))

    def _build_single_task_fallback(self, plan: dict) -> dict:
        # Fallback uses original task text if available
        tasks = plan.get("tasks", []) if isinstance(plan, dict) else []
        # Try to find original TASK.md
        original_task = ""
        try:
            original_task = (self.root / "TASK.md").read_text(encoding="utf-8")
        except Exception:
            original_task = str(plan.get("objective", "")).strip() or "Implement feature"
        # Use first meaningful line
        def _first_meaningful(t: str) -> str:
            for line in t.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if re.fullmatch(r"[-=_*]{3,}", s):
                    continue
                return s
            return "Implement feature"
        objective = _first_meaningful(original_task)
        import re as _re
        FILE_HINT_RE = re.compile(r"(?:[\w.-]+/)*[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|yml|yaml|json|toml|md|sh)")
        files_hint = list(dict.fromkeys(FILE_HINT_RE.findall(original_task)))
        return {
            "objective": objective,
            "analysis": "Fallback single-task plan",
            "tasks": [
                {
                    "id": "task001",
                    "role": "code",
                    "type": "implementation",
                    "description": original_task.strip(),
                    "acceptance": ["Task completed as described."],
                    "validation": ["Verify the task requirements are satisfied."],
                    "files": files_hint,
                }
            ],
            "risks": ["Planner structured output was unavailable; fallback single-task used."],
        }
