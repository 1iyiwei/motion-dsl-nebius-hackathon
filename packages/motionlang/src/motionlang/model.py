"""Document data model (everything except the timeline, which parse.py handles via the verb registry)."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

SPEC_VERSION = "0.1"
CAMERA_ID = "camera"

NUMERIC_PROPS = ("x", "y", "rotation", "sx", "sy", "opacity", "trim")
COLOR_PROPS = ("fill", "stroke")
ACTOR_PROPS = NUMERIC_PROPS + COLOR_PROPS
CAMERA_PROPS = ("x", "y", "zoom", "rotation")
PROP_ALIASES: dict[str, tuple[str, ...]] = {"position": ("x", "y"), "scale": ("sx", "sy")}


def expand_prop(name: str) -> tuple[str, ...]:
    return PROP_ALIASES.get(name, (name,))


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Canvas(Strict):
    width: float = Field(1280, gt=0)
    height: float = Field(720, gt=0)
    background: str = "#ffffff"
    fps: int = Field(60, ge=1, le=240)


class Stroke(Strict):
    """Raw sketch input in canvas coordinates."""

    points: list[tuple[float, float]] = Field(min_length=1)
    width: float = Field(4, gt=0)


class RectShape(Strict):
    kind: Literal["rect"]
    w: float = Field(gt=0)
    h: float = Field(gt=0)
    r: float = Field(0, ge=0, description="corner radius")


class EllipseShape(Strict):
    kind: Literal["ellipse"]
    rx: float = Field(gt=0)
    ry: float = Field(gt=0)


class PolygonShape(Strict):
    kind: Literal["polygon"]
    points: list[tuple[float, float]] = Field(min_length=3)


class PathShape(Strict):
    kind: Literal["path"]
    d: str = Field(description="SVG path data (M L H V C S Q T Z)")
    closed: bool = False


class TextShape(Strict):
    kind: Literal["text"]
    text: str = Field(min_length=1)
    size: float = Field(48, gt=0)
    font: str = "sans-serif"
    weight: Literal["normal", "bold"] = "bold"


class ImageShape(Strict):
    kind: Literal["image"]
    url: str
    w: float = Field(gt=0)
    h: float = Field(gt=0)


class StrokeShape(Strict):
    kind: Literal["stroke"]
    ref: str = Field(description="id of an entry in document.strokes")


class GroupShape(Strict):
    kind: Literal["group"]


Shape = Annotated[
    Union[RectShape, EllipseShape, PolygonShape, PathShape, TextShape, ImageShape, StrokeShape, GroupShape],
    Field(discriminator="kind"),
]


class Style(Strict):
    fill: str | None = None
    stroke: str | None = None
    stroke_width: float = Field(4, ge=0)


class Init(Strict):
    """Initial property values. Omitted x/y default to where a drawn shape sits, else canvas center."""

    x: float | None = None
    y: float | None = None
    rotation: float = 0
    sx: float = 1
    sy: float = 1
    opacity: float = Field(1, ge=0, le=1)
    trim: float = Field(1, ge=0, le=1)


class Actor(Strict):
    id: str = Field(min_length=1)
    shape: Shape
    style: Style = Field(default_factory=Style)
    init: Init = Field(default_factory=Init)
    pivot: tuple[float, float] = (0.5, 0.5)
    parent: str | None = None
    z: float = 0


# ---- constraints -------------------------------------------------------------------------

ActorSel = Union[Literal["*"], list[str]]


class InBounds(Strict):
    type: Literal["in_bounds"]
    actors: ActorSel = "*"
    margin: float = 0


class MaxSpeed(Strict):
    type: Literal["max_speed"]
    actor: str
    px_per_s: float = Field(gt=0)


class Smooth(Strict):
    type: Literal["smooth"]
    actor: str
    max_jerk: float = Field(gt=0, description="px/s^3")


class NoOverlap(Strict):
    type: Literal["no_overlap"]
    a: str
    b: str
    during: tuple[float, float] | None = None


class EndsAt(Strict):
    type: Literal["ends_at"]
    actor: str
    pos: tuple[float, float]
    t: float | None = None
    tol: float = Field(2, ge=0)


class TotalDuration(Strict):
    type: Literal["total_duration"]
    max: float | None = Field(None, gt=0)
    exact: float | None = Field(None, gt=0)
    tol: float = Field(0.05, ge=0)


Constraint = Annotated[
    Union[InBounds, MaxSpeed, Smooth, NoOverlap, EndsAt, TotalDuration],
    Field(discriminator="type"),
]


class Document(Strict):
    motion: Literal["0.1"] = SPEC_VERSION
    canvas: Canvas = Field(default_factory=Canvas)
    strokes: dict[str, Stroke] = Field(default_factory=dict)
    actors: list[Actor] = Field(default_factory=list)
    timeline: dict[str, Any] = Field(description="root block")
    constraints: list[Constraint] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list, description="non-core verb plugins, e.g. 'acme.spring_follow'")
    meta: dict[str, Any] = Field(default_factory=dict)
