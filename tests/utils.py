from __future__ import annotations

import json
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class FakeProcess:
    stdout: bytes = b""
    stderr: bytes = b""
    returncode: int = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout, self.stderr

def option_value(command: tuple[str, ...], option: str) -> str | None:
    try:
        index = command.index(option)
    except ValueError:
        return None
    if index + 1 >= len(command):
        return None
    return command[index + 1]
