from pathlib import Path

from agent_system.composition import build_default_workflow

MAX_REVIEW_ATTEMPTS = 3
MAX_CI_CORRECTIONS = 3


class ClaudeParentAgent:
    """Compatibility facade — prefer composition.build_default_workflow()."""

    def __init__(self, root=None, model=None, reasoning_model=None, coding_model=None):
        if reasoning_model is None and model is not None:
            reasoning_model = model
        self._wf = build_default_workflow(root, reasoning_model=reasoning_model, coding_model=coding_model)
        self.root = self._wf.root if hasattr(self._wf, 'root') else (root or Path.cwd())
        self.root = Path(self.root)
        self.model = reasoning_model or model
        self.reasoning_model = reasoning_model
        self.coding_model = coding_model

    def run(self, task):
        return self._wf.run(task)

    def ci_review(self, *a, **kw):
        if hasattr(self._wf, 'tech_lead') and hasattr(self._wf.tech_lead, 'ci_review'):
            return self._wf.tech_lead.ci_review(*a, **kw)
        return self._wf._parent.ci_review(*a, **kw)

    def generate_commit_message(self, *a, **kw):
        return self._wf.generate_commit_message(*a, **kw)

    def create_milestone(self, *a, **kw):
        if hasattr(self._wf, 'tech_lead') and hasattr(self._wf.tech_lead, 'create_milestone'):
            return self._wf.tech_lead.create_milestone(*a, **kw)
        if hasattr(self._wf, 'create_milestone'):
            return self._wf.create_milestone(*a, **kw)
        if hasattr(self._wf, '_parent') and hasattr(self._wf._parent, 'create_milestone'):
            return self._wf._parent.create_milestone(*a, **kw)
        return None

    def get_context(self):
        if hasattr(self._wf, 'get_context'):
            return self._wf.get_context()
        if hasattr(self._wf, 'tech_lead') and hasattr(self._wf.tech_lead, 'get_context'):
            return self._wf.tech_lead.get_context()
        from agent_system.context import load_context
        return load_context(self.root)
