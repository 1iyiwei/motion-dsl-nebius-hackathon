"""Resolve the block tree into absolutely-timed verb leaves."""

from __future__ import annotations

from dataclasses import dataclass, field

from .parse import Block, ContainerBlock, VerbBlock


@dataclass(eq=False)
class Leaf:
    block: VerbBlock
    start: float
    end: float
    order: int  # document order, breaks ties between leaves starting together
    iteration: int = 0  # >0 for repeats

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def key(self) -> tuple[float, int]:
        return (self.start, self.order)


@dataclass
class Schedule:
    leaves: list[Leaf] = field(default_factory=list)
    duration: float = 0.0
    loops: bool = False  # a repeat with count "inf" exists; one cycle is scheduled


def block_duration(b: VerbBlock) -> float:
    return b.duration if b.duration is not None else b.verb.default_duration(b.params)


def schedule(root: Block) -> Schedule:
    sched = Schedule()

    def lay(b: Block, t: float, it: int) -> float:
        s = t + b.delay
        if isinstance(b, VerbBlock):
            e = s + block_duration(b)
            sched.leaves.append(Leaf(b, s, e, len(sched.leaves), it))
            return e
        assert isinstance(b, ContainerBlock)
        if b.kind == "seq":
            for c in b.children:
                s = lay(c, s, it)
            return s
        if b.kind == "par":
            return max((lay(c, s, it) for c in b.children), default=s)
        if b.kind == "stagger":
            return max((lay(c, s + i * b.offset, it) for i, c in enumerate(b.children)), default=s)
        # repeat
        n = b.count
        if n is None:
            sched.loops = True
            n = 1
        for k in range(n):
            s = lay(b.children[0], s, it * 1000 + k) if b.children else s
        return s

    sched.duration = lay(root, 0.0, 0)
    return sched
