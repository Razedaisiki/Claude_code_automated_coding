from dataclasses import dataclass
from pathlib import Path
from typing import Literal


DeliveryMode = Literal["local", "gh"]


@dataclass
class DeliveryConfig:
    mode: DeliveryMode = "local"

    @classmethod
    def load(cls, root: Path = None) -> "DeliveryConfig":
        root = root or Path.cwd()
        p = root / ".agent" / "config.yaml"
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8")
                if "mode: gh" in text or "mode: \"gh\"" in text or "mode: 'gh'" in text:
                    return cls(mode="gh")
            except Exception:
                pass
        return cls(mode="local")

    def save(self, root: Path = None):
        root = root or Path.cwd()
        p = root / ".agent" / "config.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if p.exists():
            try:
                existing = p.read_text(encoding="utf-8")
            except Exception:
                existing = ""
        if "delivery:" in existing:
            lines = []
            in_delivery = False
            for line in existing.splitlines():
                if line.strip().startswith("delivery:"):
                    in_delivery = True
                    lines.append("delivery:")
                    lines.append(f"  mode: {self.mode}")
                    continue
                if in_delivery and line.startswith("  mode:"):
                    continue
                if in_delivery and line and not line.startswith(" ") and not line.startswith("\t"):
                    in_delivery = False
                if not in_delivery:
                    lines.append(line)
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            with p.open("a", encoding="utf-8") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(f"delivery:\n  mode: {self.mode}\n")
