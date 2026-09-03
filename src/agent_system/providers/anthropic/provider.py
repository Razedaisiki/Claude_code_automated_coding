from agent_system.providers.anthropic.config import (
    resolve_anthropic_api_key,
    resolve_anthropic_base_url,
    resolve_anthropic_model,
)


class AnthropicReasoningProvider:
    def __init__(self, model=None):
        self.model = model or resolve_anthropic_model()

    def complete(self, *, system: str, user: str, max_tokens: int, timeout: int) -> str:
        try:
            import anthropic

            api_key = resolve_anthropic_api_key()
            if not api_key:
                raise RuntimeError("Anthropic API key not configured")

            base_url = resolve_anthropic_base_url()
            if base_url:
                client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=timeout)
            else:
                client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
            kwargs = dict(
                model=self.model or "claude-sonnet-4-20250514",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": user}],
            )
            if system:
                kwargs["system"] = system
            resp = client.messages.create(**kwargs)
            parts = []
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    parts.append(block.text)
            return "\n".join(parts)
        except Exception as e:
            raise RuntimeError(f"Parent API error: {e}") from e
