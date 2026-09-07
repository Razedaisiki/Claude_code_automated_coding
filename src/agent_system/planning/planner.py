from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

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


def _normalize_string_list(value) -> List[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_enriched_task(skeleton: dict, raw: dict) -> dict:
    tid = str(skeleton.get("id", "")).strip() or f"task{skeleton.get('index', 0):03d}"
    # description: fallback to skeleton title/scope
    desc = raw.get("description")
    if not isinstance(desc, str) or not desc.strip():
        desc = str(skeleton.get("title") or skeleton.get("scope") or skeleton.get("description") or tid).strip()
    desc = desc.strip()
    # acceptance / validation / files via helper, with fallbacks
    acceptance = _normalize_string_list(raw.get("acceptance"))
    if not acceptance:
        scope = str(skeleton.get("scope") or skeleton.get("title") or tid).strip()
        acceptance = [f"The repository satisfies this task responsibility: {scope}"]
    validation = _normalize_string_list(raw.get("validation"))
    if not validation:
        validation = ["Run the relevant repository build/tests and verify the described behavior."]
    files = _normalize_string_list(raw.get("files"))
    return {
        "id": tid,
        "role": "code",
        "type": "implementation",
        "description": desc,
        "acceptance": acceptance,
        "validation": validation,
        "files": files,
    }


class StructuredPlanner:
    def __init__(self, root: Path = None, reasoning=None, config: Optional[PlannerConfig] = None):
        self.root = Path(root or Path.cwd()).resolve()
        self.reasoning = reasoning
        self.config = config or get_planner_config()

    def _invoke(self, system: str, user: str) -> str:
        return self.reasoning.complete(system=system, user=user, max_tokens=self.config.max_tokens, timeout=1200)

    def create_plan(self, task: str, ctx: ProjectContext) -> str:
        repo_map = build_repository_map(self.root)
        skeleton = self._stage_skeleton(task, ctx, repo_map)
        skeleton = self._stage_refine(skeleton, task, ctx, repo_map)
        tasks_skeleton = skeleton.get("tasks", []) if isinstance(skeleton, dict) else []
        for idx, t in enumerate(tasks_skeleton):
            if isinstance(t, dict):
                t["id"] = f"task{idx+1:03d}"
                t["index"] = idx + 1
        objective = str(skeleton.get("objective", "")).strip() if isinstance(skeleton, dict) else ""
        analysis = str(skeleton.get("analysis", "")).strip() if isinstance(skeleton, dict) else ""
        enriched_tasks = self._stage_enrich(tasks_skeleton, task, ctx, repo_map)
        final_tasks = enriched_tasks
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
        errors = validate_plan_data(plan, max_tasks=self.config.max_tasks)
        if errors:
            _write_debug(self.root, "validation-errors.json", json.dumps(errors, indent=2, ensure_ascii=False))
            print("Planner internal invariant failure:")
            for e in errors:
                print(f"  - {e}")
            raise PlanningError("planning failed: " + "; ".join(errors))
        _write_debug(self.root, "final.json", json.dumps(plan, indent=2, ensure_ascii=False))
        print(f"Planner: final plan contains {len(final_tasks)} tasks")
        print("Planner: final validation passed")
        return json.dumps(plan, ensure_ascii=False)

    def _stage_skeleton(self, task: str, ctx: ProjectContext, repo_map: str) -> dict:
        system = _load_prompt("planning_skeleton")
        if not system:
            system = "Create a compact task skeleton."
        system = system.replace("1-32", f"1-{self.config.max_tasks}")
        if "{{max_tasks}}" in system:
            system = system.replace("{{max_tasks}}", str(self.config.max_tasks))
        # Provide dynamic limit hint in user prompt instead of relying solely on prompt text
        common = ""
        try:
            p = Path(__file__).parent.parent / "prompts" / "common" / "engineering_rules.md"
            if p.exists():
                common = p.read_text(encoding="utf-8")
                system = system + "\n\n" + common
        except Exception:
            pass
        milestones_text = "\n\n".join(f"## {m.name}\n{m.content}" for m in (ctx.milestones or []))
        user = (
            f"Original Task:\n{task}\n\n"
            f"Repository Map:\n{repo_map}\n\n"
            f"CLAUDE.md:\n{ctx.instructions or '(none)'}\n\n"
            f"Milestones:\n{milestones_text or '(none)'}\n\n"
            f"Configured safety limit: {self.config.max_tasks} tasks."
        )
        raw = self._invoke(system, user)
        _write_debug(self.root, "skeleton.raw.txt", raw)
        obj = extract_json_object(raw)
        _write_debug(self.root, "skeleton.json", json.dumps(obj, indent=2, ensure_ascii=False) if obj else raw)
        if obj is None or not isinstance(obj.get("tasks"), list) or not obj["tasks"]:
            obj = self._repair_skeleton(raw, task, ctx, repo_map)
        if obj is None or not isinstance(obj.get("tasks"), list) or not obj["tasks"]:
            raise PlanningError(f"planning failed: skeleton generation failed — no tasks produced. Raw: {raw[:500]}")
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
            raise PlanningError(f"skeleton produced {len(skeleton_tasks)} tasks, safety limit is {self.config.max_tasks}")
        print(f"Planner: skeleton produced {len(skeleton_tasks)} tasks")
        return {"objective": str(obj.get("objective", "")).strip() or task.strip().splitlines()[0][:200], "analysis": str(obj.get("analysis", "")).strip(), "tasks": skeleton_tasks}

    def _repair_skeleton(self, raw: str, task: str, ctx: ProjectContext, repo_map: str) -> Optional[dict]:
        for attempt in range(1, self.config.repair_attempts + 1):
            errors = ["Skeleton JSON invalid or missing tasks"]
            schema = '{"objective":"...","tasks":[{"title":"...","scope":"..."}]}'
            repair_system = _load_prompt("planning_repair")
            repair_user = repair_system.replace("{{errors}}", "\n".join(f"- {e}" for e in errors)).replace("{{raw}}", raw).replace("{{schema}}", schema)
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
            compact = json.dumps({"objective": objective, "tasks": tasks}, ensure_ascii=False)
            user = f"Original Task:\n{task}\n\nCurrent skeleton ({len(tasks)} tasks):\n{compact}\n\nRepository Map:\n{repo_map}"
            raw = self._invoke(system, user)
            _write_debug(self.root, f"refine-{rnd:02d}.raw.txt", raw)
            obj = extract_json_object(raw)
            if obj is None:
                _write_debug(self.root, f"refine-{rnd:02d}.error.txt", "invalid JSON")
                print(f"Planner: refinement round {rnd} invalid; preserving previous {len(tasks)}-task skeleton")
                break
            _write_debug(self.root, f"refine-{rnd:02d}.json", json.dumps(obj, indent=2, ensure_ascii=False))
            changed = bool(obj.get("changed", False))
            new_tasks_raw = obj.get("tasks", [])
            if not isinstance(new_tasks_raw, list) or not new_tasks_raw:
                print(f"Planner: refinement round {rnd} — no tasks, keeping previous")
                break
            new_tasks = []
            for t in new_tasks_raw:
                if not isinstance(t, dict):
                    continue
                title = str(t.get("title") or t.get("description") or t.get("name") or "").strip()
                scope = str(t.get("scope") or t.get("analysis") or "").strip()
                if not title:
                    continue
                new_tasks.append({"title": title, "scope": scope or title})
            if len(new_tasks) < len(tasks):
                print(f"Planner: refinement round {rnd} attempted to reduce task count {len(tasks)} -> {len(new_tasks)}; keeping previous {len(tasks)}-task skeleton")
                break
            if len(new_tasks) > self.config.max_tasks:
                print(f"Planner: refinement round {rnd} produced {len(new_tasks)} tasks exceeding limit {self.config.max_tasks}; keeping previous {len(tasks)}-task skeleton")
                break
            if not changed:
                print(f"Planner: refinement round {rnd} -> {len(tasks)} tasks (converged)")
                break
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
            user = (
                f"Original Task:\n{task}\n\n"
                f"Repository Map:\n{repo_map}\n\n"
                f"Enrich batch {batch_idx+1}/{total_batches} ({batch_ids[0] if batch_ids else ''}-{batch_ids[-1] if batch_ids else ''}):\n"
                f"{skeleton_json}\n\n"
                f"Return enriched tasks for this batch only. Exactly {len(batch)} tasks in order {', '.join(batch_ids)}."
            )
            print(f"Planner: enriching tasks {batch_ids[0] if batch_ids else ''}-{batch_ids[-1] if batch_ids else ''} (batch {batch_idx+1}/{total_batches})")
            raw = self._invoke(system, user)
            _write_debug(self.root, f"enrich-{start+1:03d}-{start+len(batch):03d}.raw.txt", raw)
            obj = extract_json_object(raw)
            if obj is None or not isinstance(obj.get("tasks"), list):
                obj = self._repair_batch(raw, batch, batch_idx)
                if obj is None:
                    raise PlanningError(f"planning failed: enrichment batch {batch_idx+1} returned invalid JSON. Raw: {raw[:500]}")
            tasks_raw = obj.get("tasks", [])
            if not isinstance(tasks_raw, list) or len(tasks_raw) != len(batch):
                obj2 = self._repair_batch(raw, batch, batch_idx)
                if obj2 is not None and isinstance(obj2.get("tasks"), list) and len(obj2.get("tasks", [])) == len(batch):
                    tasks_raw = obj2.get("tasks", [])
                else:
                    raise PlanningError(f"planning failed: enrichment batch {batch_idx+1} expected {len(batch)} tasks got {len(tasks_raw) if isinstance(tasks_raw, list) else 'invalid'}")
            _write_debug(self.root, f"enrich-{start+1:03d}-{start+len(batch):03d}.json", json.dumps(obj, indent=2, ensure_ascii=False))
            normalized_batch: List[dict] = []
            for expected, produced in zip(batch, tasks_raw):
                if not isinstance(produced, dict):
                    produced = {}
                normalized = _normalize_enriched_task(expected, produced)
                normalized_batch.append(normalized)
            # Batch-level validation (local normalization only, no LLM)
            errors = validate_plan_data({"tasks": normalized_batch}, max_tasks=len(normalized_batch))
            if errors:
                # Fix trivial string/list issues deterministically without LLM
                for t in normalized_batch:
                    t["acceptance"] = _normalize_string_list(t.get("acceptance")) or [f"The repository satisfies: {t.get('description','')[:80]}"]
                    t["validation"] = _normalize_string_list(t.get("validation")) or ["Run relevant tests and verify described behavior."]
                    t["files"] = _normalize_string_list(t.get("files"))
                    if not t.get("description", "").strip():
                        t["description"] = str(t.get("id", "")) + " " + str(batch[0].get("title", ""))
                errors2 = validate_plan_data({"tasks": normalized_batch}, max_tasks=len(normalized_batch))
                if errors2:
                    # Only then try batch repair
                    obj3 = self._repair_batch(raw, batch, batch_idx)
                    if obj3 is not None:
                        tasks_raw2 = obj3.get("tasks", [])
                        if isinstance(tasks_raw2, list) and len(tasks_raw2) == len(batch):
                            normalized_batch = [_normalize_enriched_task(b, r if isinstance(r, dict) else {}) for b, r in zip(batch, tasks_raw2)]
                            errors3 = validate_plan_data({"tasks": normalized_batch}, max_tasks=len(normalized_batch))
                            if not errors3:
                                print(f"Planner: enrichment batch {batch_idx+1} repaired")
                            else:
                                raise PlanningError(f"planning failed: enrichment batch {batch_idx+1} validation failed: {errors3}")
                        else:
                            raise PlanningError(f"planning failed: enrichment batch {batch_idx+1} validation failed: {errors2}")
                    else:
                        raise PlanningError(f"planning failed: enrichment batch {batch_idx+1} validation failed: {errors2}")
            print(f"Planner: enrichment batch {batch_idx+1} valid")
            enriched.extend(normalized_batch)
        return enriched

    def _repair_batch(self, raw: str, batch: List[dict], batch_idx: int) -> Optional[dict]:
        for attempt in range(1, self.config.repair_attempts + 1):
            expected_ids = [b.get("id") for b in batch]
            expected_desc = "\n".join(f"{b.get('id')}: {b.get('title','')} — {b.get('scope','')}" for b in batch)
            schema = '{"tasks":[{"id":"task006","description":"...","acceptance":["..."],"validation":["..."],"files":[]}]}'
            errors = [f"Batch {batch_idx+1} invalid JSON or expected {len(batch)} tasks {expected_ids}"]
            repair_system = _load_prompt("planning_repair")
            repair_user = (
                repair_system.replace("{{errors}}", "\n".join(f"- {e}" for e in errors))
                .replace("{{raw}}", raw)
                .replace("{{schema}}", schema)
                + f"\n\nExpected tasks:\n{expected_desc}\nThe corrected response MUST contain exactly {len(batch)} tasks in exactly this order: {', '.join(expected_ids)}."
            )
            try:
                repaired = self._invoke(repair_system, repair_user)
                _write_debug(self.root, f"repair-enrich-{batch_idx+1}-{attempt}.raw.txt", repaired)
                obj = extract_json_object(repaired)
                if obj is not None and isinstance(obj.get("tasks"), list) and len(obj.get("tasks", [])) == len(batch):
                    print(f"Planner: repairing enrichment batch {batch_idx+1} (attempt {attempt})")
                    return obj
            except Exception as e:
                _write_debug(self.root, f"repair-enrich-{batch_idx+1}-{attempt}.error.txt", str(e))
        return None
