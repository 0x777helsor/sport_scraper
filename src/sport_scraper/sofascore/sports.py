from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ScheduleKind = Literal["tournament-index", "sport-root-scheduled"]
SideKind = Literal["team", "player"]


@dataclass(frozen=True, slots=True)
class Sport:
    slug: str
    name: str
    side_kind: SideKind
    schedule_kind: ScheduleKind = "tournament-index"


SPORT_REGISTRY = {
    "football": Sport(slug="football", name="Football", side_kind="team"),
    "basketball": Sport(slug="basketball", name="Basketball", side_kind="team"),
    "baseball": Sport(slug="baseball", name="Baseball", side_kind="team"),
    "american-football": Sport(slug="american-football", name="American Football", side_kind="team"),
    "tennis": Sport(slug="tennis", name="Tennis", side_kind="player"),
    "cricket": Sport(slug="cricket", name="Cricket", side_kind="team"),
}

SPORT_SLUGS: tuple[str, ...] = tuple(SPORT_REGISTRY)
SPORT_NAMES: dict[str, str] = {s.slug: s.name for s in SPORT_REGISTRY.values()}
