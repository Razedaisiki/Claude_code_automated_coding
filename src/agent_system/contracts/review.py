from dataclasses import dataclass, field
from typing import List, Optional

MAX_REVIEW_TEXT_BYTES = 512 * 1024


@dataclass
class CandidateSnapshot:
    task_id: str
    review_attempt: int
    reviewed_tree_sha: str
    base_commit_sha: str
    base_tree_sha: str
    changed_files: List[str] = field(default_factory=list)
    project_diff: str = ""
    result_status: str = ""
    result_message: str = ""
    result_artifacts: List[str] = field(default_factory=list)
    execution_status: str = "COMPLETED"
    stop_reason: Optional[str] = None
    execution_evidence: Optional[dict] = None


@dataclass
class ReviewFileEvidence:
    path: str
    kind: str
    blob_sha: Optional[str] = None
    size: Optional[int] = None
    content: Optional[str] = None
    symlink_target: Optional[str] = None
    binary: bool = False
    missing: bool = False


@dataclass
class ReviewPackage:
    package_version: int
    session_id: str
    task_id: str
    original_task_id: str
    review_attempt: int
    reviewed_tree_sha: str
    base_commit_sha: str
    base_tree_sha: str
    changed_files: List[str] = field(default_factory=list)
    project_diff: str = ""
    relevant_files: List[ReviewFileEvidence] = field(default_factory=list)
    candidate: Optional[CandidateSnapshot] = None
    validation: Optional[dict] = None
    task_baseline: Optional[dict] = None
