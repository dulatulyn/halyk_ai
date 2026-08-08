from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Diag:
    counters: Counter = field(default_factory=Counter)
    events: list[tuple[str, str]] = field(default_factory=list)

    def bump(self, key: str, n: int = 1) -> None:
        self.counters[key] += n

    def note(self, kind: str, detail: str = "") -> None:
        self.events.append((kind, detail))
        self.counters[kind] += 1

    def reset(self) -> None:
        self.counters.clear()
        self.events.clear()

    def render(self) -> str:
        lines = ["ДИАГНОСТИКА", ""]
        for key in sorted(self.counters):
            lines.append(f"  {self.counters[key]:5d}  {key}")
        if self.events:
            lines += ["", "СОБЫТИЯ"]
            for kind, detail in self.events:
                lines.append(f"  {kind}: {detail}" if detail else f"  {kind}")
        return "\n".join(lines)


DIAG = Diag()
