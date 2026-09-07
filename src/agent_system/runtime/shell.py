import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union


@dataclass
class ShellResult:
    returncode: int
    stdout: str
    stderr: str


class Shell:
    def __init__(self, root: Path = None, timeout: int = 300):
        self.root = Path(root or Path.cwd()).resolve()
        self.timeout = timeout

    def run(self, cmd: Union[str, List[str]], timeout: Optional[int] = None, env=None, input_text: Optional[str] = None) -> ShellResult:
        to = timeout if timeout is not None else self.timeout
        use_shell = isinstance(cmd, str)
        try:
            proc = subprocess.run(
                cmd,
                shell=use_shell,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=to,
                env=env,
                input=input_text,
            )
            return ShellResult(returncode=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or "")
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            stderr = (err + f"\nCommand timed out after {to}s").strip()
            return ShellResult(returncode=124, stdout=out, stderr=stderr)
