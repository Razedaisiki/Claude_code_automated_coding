import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from agent_system.runtime.atomic_io import atomic_write_bytes, atomic_write_json, fsync_parent_dir

CURRENT_SCHEMA_VERSION = 5

ALLOWED_STATUSES = {
    "INITIALIZED", "RUNNING", "REVIEW_PENDING", "COMMITTING",
    "PUSHING", "WAITING_CI", "CI_PASSED", "CI_FAILED", "COMPLETED", "FAILED",
}

UNFINISHED_PHASES = {
    "EXECUTING", "VALIDATING", "REVIEWING", "COMMITTING", "PUSHING",
    "CI_DISCOVERY", "WAITING_CI", "CI_REVIEW", "CORRECTING",
}


class StateManager:
    def __init__(self, root: Path = None):
        self.root = Path(root or Path.cwd()).resolve()
        self.state_file = self.root / ".agent" / "state.json"
        self.previous_file = self.root / ".agent" / "state.previous.json"
        self.log_file = self.root / ".agent" / "state.log.jsonl"

    def _read_json_file(self, path: Path):
        try:
            if not path.exists():
                return None
            raw = path.read_bytes()
            if not raw.strip():
                return None
            data = json.loads(raw.decode("utf-8"))
            return data
        except Exception:
            return None

    def _latest_valid_from_log(self):
        if not self.log_file.exists():
            return None
        try:
            raw = self.log_file.read_bytes().decode("utf-8", errors="ignore")
            lines = raw.splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    st = entry.get("state")
                    if isinstance(st, dict) and "schema_version" in st:
                        return st
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _repair_log_truncation(self):
        if not self.log_file.exists():
            return
        try:
            raw = self.log_file.read_bytes()
            text = raw.decode("utf-8", errors="ignore")
            if not text:
                return
            ends_with_newline = text.endswith("\n")
            lines = text.splitlines(True)
            valid_up_to = 0
            buf = ""
            for chunk in lines:
                buf += chunk
                stripped = chunk.strip()
                if not stripped:
                    valid_up_to = len(buf)
                    continue
                try:
                    json.loads(stripped)
                    valid_up_to = len(buf)
                except Exception:
                    break
            if valid_up_to < len(text):
                truncated = text[:valid_up_to]
                atomic_write_bytes(self.log_file, truncated.encode("utf-8"))
        except Exception:
            pass

    def _ensure_history_complete(self, state: dict):
        try:
            rev = int(state.get("revision", 0) or 0)
            if not self.log_file.exists():
                if rev > 0:
                    sha = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
                    entry = {"revision": rev, "timestamp": state.get("updated_at") or datetime.now(timezone.utc).isoformat(), "event": "state_recovered", "state_sha256": sha, "state": state}
                    line = json.dumps(entry, ensure_ascii=False) + "\n"
                    self.log_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(self.log_file, "a", encoding="utf-8") as f:
                        f.write(line)
                        f.flush()
                        try:
                            os.fsync(f.fileno())
                        except Exception:
                            pass
                    fsync_parent_dir(self.log_file)
                return
            raw = self.log_file.read_bytes().decode("utf-8", errors="ignore")
            max_rev = 0
            for l in raw.splitlines():
                l = l.strip()
                if not l:
                    continue
                try:
                    e = json.loads(l)
                    r = int(e.get("revision", 0) or 0)
                    if r > max_rev:
                        max_rev = r
                except Exception:
                    continue
            if max_rev < rev:
                sha = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
                entry = {"revision": rev, "timestamp": state.get("updated_at") or datetime.now(timezone.utc).isoformat(), "event": "state_recovered", "state_sha256": sha, "state": state}
                line = json.dumps(entry, ensure_ascii=False) + "\n"
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:
                        pass
                fsync_parent_dir(self.log_file)
        except Exception:
            pass

    def _migrate_v4(self, data: dict):
        if data.get("schema_version") != 4:
            return None
        status = data.get("status", "")
        delivery = data.get("delivery") or {}
        phase = delivery.get("phase") if isinstance(delivery, dict) else None
        is_terminal = status in ("INITIALIZED", "COMPLETED") or not phase or phase == "TASK_COMPLETED"
        if not is_terminal and phase in UNFINISHED_PHASES:
            raise RuntimeError(f"Legacy active checkpoint cannot be safely resumed under schema v5. (phase={phase} status={status}) Please reset workspace or complete with previous version.")
        migrated = dict(data)
        migrated["schema_version"] = CURRENT_SCHEMA_VERSION
        migrated["revision"] = int(migrated.get("revision", 0) or 0)
        migrated["updated_at"] = datetime.now(timezone.utc).isoformat()
        if "task_history" not in migrated:
            migrated["task_history"] = []
        if "task_sha256" not in migrated:
            migrated["task_sha256"] = ""
        if "plan_sha256" not in migrated:
            migrated["plan_sha256"] = ""
        if "workflow_start_sha" not in migrated:
            migrated["workflow_start_sha"] = ""
        if "delivery" not in migrated:
            migrated["delivery"] = {}
        return migrated

    def load(self) -> dict:
        if not self.state_file.exists():
            base = {"status": "INITIALIZED", "session_id": None, "schema_version": CURRENT_SCHEMA_VERSION, "revision": 0, "updated_at": datetime.now(timezone.utc).isoformat(), "task_history": [], "delivery": {}, "task_sha256": "", "plan_sha256": "", "workflow_start_sha": ""}
            return base
        data = self._read_json_file(self.state_file)
        if data is not None:
            if "schema_version" not in data:
                data["schema_version"] = 1
            if data.get("schema_version") == 4:
                try:
                    migrated = self._migrate_v4(data)
                    if migrated is not None:
                        self.save(migrated, event="migrated_v4_to_v5")
                        return migrated
                except RuntimeError:
                    raise
                except Exception as e:
                    raise RuntimeError(str(e)) from e
            if data.get("schema_version") != CURRENT_SCHEMA_VERSION:
                raise RuntimeError(f"schema_version mismatch: {data.get('schema_version')} != {CURRENT_SCHEMA_VERSION}")
            if isinstance(data.get("revision"), int) and isinstance(data.get("updated_at"), str):
                self._repair_log_truncation()
                self._ensure_history_complete(data)
            return data
        prev = self._read_json_file(self.previous_file)
        log_state = self._latest_valid_from_log()
        candidates = []
        if prev is not None:
            if prev.get("schema_version") == 4:
                try:
                    prev = self._migrate_v4(prev)
                except RuntimeError:
                    prev = None
            if prev and prev.get("schema_version") == CURRENT_SCHEMA_VERSION:
                candidates.append(prev)
        if log_state is not None:
            if log_state.get("schema_version") == 4:
                try:
                    log_state = self._migrate_v4(log_state)
                except RuntimeError:
                    log_state = None
            if log_state and log_state.get("schema_version") == CURRENT_SCHEMA_VERSION:
                candidates.append(log_state)
        if not candidates:
            self._repair_log_truncation()
            log_state2 = self._latest_valid_from_log()
            if log_state2 and log_state2.get("schema_version") == CURRENT_SCHEMA_VERSION:
                candidates.append(log_state2)
        if candidates:
            def rev_key(x):
                try:
                    return int(x.get("revision", 0) or 0)
                except Exception:
                    return 0
            best = max(candidates, key=rev_key)
            try:
                atomic_write_json(self.state_file, best)
                sha = hashlib.sha256(json.dumps(best, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
                entry = {"revision": best.get("revision", 0), "timestamp": datetime.now(timezone.utc).isoformat(), "event": "state_recovered", "state_sha256": sha, "state": best}
                line = json.dumps(entry, ensure_ascii=False) + "\n"
                self.log_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:
                        pass
                fsync_parent_dir(self.log_file)
            except Exception:
                pass
            self._repair_log_truncation()
            return best
        raise RuntimeError("state unrecoverable: state.json corrupted and no valid backup found")

    def save(self, state: dict, event: str = "state_saved"):
        if "schema_version" not in state:
            state["schema_version"] = CURRENT_SCHEMA_VERSION
        new_state = dict(state)
        new_state["schema_version"] = CURRENT_SCHEMA_VERSION
        base_rev = 0
        try:
            if self.state_file.exists():
                raw = self.state_file.read_bytes()
                if raw.strip():
                    try:
                        prev_data = json.loads(raw.decode("utf-8"))
                        base_rev = int(prev_data.get("revision", 0) or 0)
                    except Exception:
                        base_rev = 0
                    try:
                        atomic_write_bytes(self.previous_file, raw)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            passed_rev = int(new_state.get("revision", 0) or 0)
            if passed_rev > base_rev:
                base_rev = passed_rev
        except Exception:
            pass
        new_rev = base_rev + 1
        new_state["revision"] = new_rev
        new_state["updated_at"] = datetime.now(timezone.utc).isoformat()
        if "task_history" not in new_state:
            new_state["task_history"] = []
        if "delivery" not in new_state:
            new_state["delivery"] = {}
        if "task_sha256" not in new_state:
            new_state["task_sha256"] = new_state.get("task_sha256", "") or ""
        if "plan_sha256" not in new_state:
            new_state["plan_sha256"] = new_state.get("plan_sha256", "") or ""
        if "workflow_start_sha" not in new_state:
            new_state["workflow_start_sha"] = new_state.get("workflow_start_sha", "") or ""
        try:
            canonical = json.dumps(new_state, sort_keys=True, ensure_ascii=False)
            sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        except Exception:
            sha = ""
        atomic_write_json(self.state_file, new_state)
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            entry = {"revision": new_rev, "timestamp": new_state["updated_at"], "event": event, "state_sha256": sha, "state": new_state}
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            fsync_parent_dir(self.log_file)
        except Exception:
            pass
        return new_state

    def start_new_execution(self, session_id: str, task_sha256: str = "", plan_sha256: str = "", workflow_start_sha: str = "") -> dict:
        state = self.load()
        state["schema_version"] = CURRENT_SCHEMA_VERSION
        state["status"] = "RUNNING"
        state["session_id"] = session_id
        state["execution_mode"] = "NEW"
        state["delivery"] = {}
        state["task_history"] = state.get("task_history") or []
        if task_sha256:
            state["task_sha256"] = task_sha256
        if plan_sha256:
            state["plan_sha256"] = plan_sha256
        if workflow_start_sha:
            state["workflow_start_sha"] = workflow_start_sha
        self.save(state, event="workflow_started")
        return state

    def replace_delivery(self, delivery: dict) -> dict:
        state = self.load()
        state["delivery"] = dict(delivery)
        self.save(state, event="replace_delivery")
        return state

    def update(self, event: str = "state_updated", **kwargs) -> dict:
        if "event" in kwargs:
            event = kwargs.pop("event")
        state = self.load()
        if "delivery" in kwargs and isinstance(kwargs["delivery"], dict) and isinstance(state.get("delivery"), dict):
            merged = dict(state["delivery"])
            merged.update(kwargs["delivery"])
            kwargs["delivery"] = merged
        state.update(kwargs)
        self.save(state, event=event)
        return state

    def update_delivery(self, event: str = "update_delivery", **fields) -> dict:
        state = self.load()
        delivery = dict(state.get("delivery") or {})
        delivery.update(fields)
        state["delivery"] = delivery
        self.save(state, event=event)
        return state

    def validate(self) -> None:
        raise RuntimeError(
            "StateManager.validate() is deprecated — use Checkpoint(root).validate() directly. "
            "Kept for supervisor/state.py compat only; import Checkpoint for Runtime validation."
        )
