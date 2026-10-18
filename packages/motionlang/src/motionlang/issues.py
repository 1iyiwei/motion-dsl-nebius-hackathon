"""Structured validation issues, written to be pasted straight into an LLM repair prompt."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

CODES = (
    "E_SCHEMA", "E_DUP_ID", "E_REF", "E_CONFLICT", "E_DISCONTINUITY", "E_BOUNDS", "E_SPEED",
    "E_JERK", "E_OVERLAP", "E_ENDS_AT", "E_DURATION", "E_EDIT", "E_NOOP", "E_INVISIBLE",
)


@dataclass
class Issue:
    code: str
    message: str
    hint: str = ""
    severity: Literal["error", "warning"] = "error"
    block: str | None = None
    actor: str | None = None
    t: float | None = None
    pointer: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d["t"] is not None:
            d["t"] = round(d["t"], 3)
        return {k: v for k, v in d.items() if v not in (None, "")}

    def __str__(self) -> str:
        where = " ".join(f"{k}={v}" for k, v in (("block", self.block), ("actor", self.actor)) if v)
        if not where and self.pointer:  # say where, so a repair can find it
            where = f"at {self.pointer}"
        at = f" at t={self.t:.2f}s" if self.t is not None and f"t={self.t:.2f}s" not in self.message else ""
        hint = f" (hint: {self.hint})" if self.hint else ""
        return f"{self.code}{' [' + where + ']' if where else ''}{at}: {self.message}{hint}"


@dataclass
class Report:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, issue: Issue) -> None:
        (self.warnings if issue.severity == "warning" else self.errors).append(issue)

    def extend(self, issues: list[Issue]) -> None:
        for i in issues:
            self.add(i)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "stats": self.stats,
        }

    def summary(self) -> str:
        if self.ok and not self.warnings:
            return "ok"
        return "\n".join(str(i) for i in [*self.errors, *self.warnings])
