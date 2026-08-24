import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ShellResult:
    returncode: int
    stdout: str
    stderr: str


class Shell:
    def __init__(self, root: Path = None, timeout: int = 30):
        self.root = root or Path.cwd()
        self.timeout = timeout

    def run(self, cmd: str, timeout: Optional[int] = None) -> ShellResult:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(self.root),
            capture_output=True,
            text=True,
            timeout=timeout or self.timeout,
        )
        return ShellResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
