import os
from dataclasses import dataclass


def _parse_int_env(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = int(raw.strip())
    except Exception:
        return default
    if val < lo:
        return lo
    if val > hi:
        return hi
    return val


@dataclass(frozen=True)
class PlannerConfig:
    max_tasks: int = 64
    max_tokens: int = 32000
    split_rounds: int = 3
    enrich_batch_size: int = 5
    repair_attempts: int = 2
    fallback_mode: str = "error"


def get_planner_config() -> PlannerConfig:
    max_tasks = _parse_int_env("WORKFLOW_PLANNER_MAX_TASKS", 64, 1, 100)
    max_tokens = _parse_int_env("WORKFLOW_PLANNER_MAX_TOKENS", 32000, 1024, 64000)
    split_rounds = _parse_int_env("WORKFLOW_PLANNER_SPLIT_ROUNDS", 3, 0, 5)
    batch_size = _parse_int_env("WORKFLOW_PLANNER_ENRICH_BATCH_SIZE", 5, 1, 10)
    repair_attempts = _parse_int_env("WORKFLOW_PLANNER_REPAIR_ATTEMPTS", 2, 0, 5)
    fallback_raw = os.getenv("WORKFLOW_PLANNER_FALLBACK_MODE", "error")
    fallback = fallback_raw.strip().lower() if isinstance(fallback_raw, str) else "error"
    if fallback not in ("error", "single_task"):
        fallback = "error"
    return PlannerConfig(
        max_tasks=max_tasks,
        max_tokens=max_tokens,
        split_rounds=split_rounds,
        enrich_batch_size=batch_size,
        repair_attempts=repair_attempts,
        fallback_mode=fallback,
    )
