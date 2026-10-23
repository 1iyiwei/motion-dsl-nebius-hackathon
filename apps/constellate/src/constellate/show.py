"""The show script: what Nemotron writes. Artistic choices only; drone paths come from the compiler.

Units are metres on a vertical show plane: x across (0 = left edge of the airspace), z up (0 = the lowest
allowed altitude). The compiler maps them to MotionLang canvas pixels.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

HEX = r"^#[0-9a-fA-F]{6}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Limits(Strict):
    """Flight envelope and airspace. Defaults are typical show drones; a site briefing can tighten them."""

    width: float = Field(128.0, gt=0, description="airspace width, m")
    height: float = Field(72.0, gt=0, description="airspace height above the lowest allowed altitude, m")
    margin: float = Field(2.0, ge=0, description="keep-out band inside the airspace edges, m")
    min_separation: float = Field(1.5, gt=0, description="minimum distance between any two drones, m")
    max_speed: float = Field(8.0, gt=0, description="m/s")
    max_accel: float = Field(6.0, gt=0, description="m/s^2")
    max_flight_time: float = Field(900.0, gt=0, description="battery endurance, s")
    battery_reserve: float = Field(0.2, ge=0, lt=1, description="fraction of endurance kept in reserve at landing")
    design_wind: float = Field(6.0, ge=0, description="mean wind the show must be certified for in simulation, m/s")
    near_miss: float = Field(1.0, gt=0, description="simulated distance between drones that counts as a near miss, m")
    max_tracking_error: float = Field(1.0, gt=0, description="largest simulated drift from the planned path, m")
    crowd_setback: float = Field(30.0, gt=0, description="distance from the show plane to the nearest spectators, m")

    @property
    def show_time_budget(self) -> float:
        return self.max_flight_time * (1 - self.battery_reserve)


class Placement(Strict):
    center: tuple[float, float] = Field((0.5, 0.5), description="formation centre as fractions of the airspace [x, z]")
    size: float = Field(0.6, gt=0, le=1, description="formation height as a fraction of the airspace height (width follows the aspect ratio)")


class PresetFormation(Placement):
    kind: Literal["preset"]
    shape: Literal["heart", "star", "ring", "circle", "grid", "spiral", "wave"]
    fill: Literal["outline", "fill"] = "outline"


class StrokeFormation(Placement):
    kind: Literal["stroke"]
    stroke: str = Field(description="id of a drawn stroke in `sources.strokes`")
    fill: Literal["outline", "fill"] = "outline"


class SvgFormation(Placement):
    kind: Literal["svg"]
    svg: str = Field(description="id of an SVG in `sources.svgs`")
    fill: Literal["outline", "fill"] = "fill"
    keep_colors: bool = Field(True, description="light each drone with the colour of the artwork under it")


class TextFormation(Placement):
    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=24)
    fill: Literal["outline", "fill"] = "fill"


Formation = Annotated[Union[PresetFormation, StrokeFormation, SvgFormation, TextFormation], Field(discriminator="kind")]


class FormStep(Strict):
    kind: Literal["form"]
    id: str
    formation: str = Field(description="id in `formations`")
    transition: float = Field(4.0, gt=0, description="seconds to fly from the previous formation")
    style: Literal["direct", "stagger", "expand"] = Field(
        "direct", description="direct: all drones fly straight and arrive together (provably collision-free); "
        "stagger: drones leave one after another (a ripple; can cause near misses); "
        "expand: the old formation first spreads out, then everyone flies in")
    hold: float = Field(2.0, ge=0, description="seconds to hold the formation after arriving")
    color: str | None = Field(None, pattern=HEX, description="LED colour; default keeps artwork colours or the current colour")


class SpinStep(Strict):
    kind: Literal["spin"]
    id: str
    turns: float = Field(description="revolutions about the formation centre; negative = counter-clockwise")
    duration: float = Field(gt=0)


class ColorStep(Strict):
    kind: Literal["color"]
    id: str
    color: str = Field(pattern=HEX)
    duration: float = Field(1.0, gt=0)


class HoldStep(Strict):
    kind: Literal["hold"]
    id: str
    duration: float = Field(gt=0)


Step = Annotated[Union[FormStep, SpinStep, ColorStep, HoldStep], Field(discriminator="kind")]


class Sources(Strict):
    strokes: dict[str, list[tuple[float, float]]] = Field(default_factory=dict, description="drawn strokes, points in metres [x, z]")
    svgs: dict[str, str] = Field(default_factory=dict, description="SVG markup (or a file path, for the CLI)")


class Show(Strict):
    title: str = "Untitled show"
    drones: int = Field(100, ge=4, le=500)
    color: str = Field("#ffffff", pattern=HEX, description="starting LED colour")
    limits: Limits = Field(default_factory=Limits)
    formations: dict[str, Formation]
    steps: list[Step] = Field(min_length=1)
    land: bool = Field(True, description="return to the launch grid at the end")
    sources: Sources = Field(default_factory=Sources)

    @model_validator(mode="after")
    def _refs(self):  # type: ignore[no-untyped-def]
        ids = [s.id for s in self.steps]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"duplicate step ids: {sorted(dup)}")
        if self.steps[0].kind != "form":
            raise ValueError("the first step must be a `form` step (drones take off into it)")
        for s in self.steps:
            if s.kind == "form" and s.formation not in self.formations:
                raise ValueError(f"step {s.id!r} uses unknown formation {s.formation!r}; defined: {sorted(self.formations)}")
        for fid, f in self.formations.items():
            if f.kind == "stroke" and f.stroke not in self.sources.strokes:
                raise ValueError(f"formation {fid!r} uses unknown stroke {f.stroke!r}")
            if f.kind == "svg" and f.svg not in self.sources.svgs:
                raise ValueError(f"formation {fid!r} uses unknown svg {f.svg!r}")
        return self
