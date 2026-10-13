from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, TypedDict, NotRequired

SportSlug = Literal[
    "football",
    "basketball",
    "american-football",
    "baseball",
    "cricket",
    "tennis",
]

StatusType = Literal[
    "notstarted",
    "inprogress",
    "finished",
    "postponed",
    "cancelled",
    "suspended",
    "interrupted",
    "abandoned",
    "retired",
    "walkover",
    "deducted",
    "lostbywalkover",
]


class Team(TypedDict):
    id: int
    name: str
    slug: NotRequired[str]
    shortName: NotRequired[str]
    country: NotRequired[str]
    flag: NotRequired[str]
    isWinner: NotRequired[bool | None]


class Player(TypedDict):
    id: int
    name: str
    firstName: NotRequired[str]
    lastName: NotRequired[str]
    shortName: NotRequired[str]
    country: NotRequired[str]
    isWinner: NotRequired[bool | None]


class Score(TypedDict, total=False):
    current: int | None
    period1: int | None
    period2: int | None
    period3: int | None
    period4: int | None
    overtime: int | None
    innings: int | None
    runs: int | None
    wickets: int | None
    overs: float | None
    # tennis specific
    sets: int | None
    games: int | None
    points: int | None


class EventStatus(TypedDict):
    type: StatusType
    description: NotRequired[str]
    code: NotRequired[int]


class Event(TypedDict, total=False):
    id: int
    uid: NotRequired[str]
    name: NotRequired[str]
    slug: NotRequired[str]
    sport: SportSlug
    startTimestamp: int  # unix seconds
    startDateTime: NotRequired[str]
    status: EventStatus
    tournament: dict[str, Any]
    uniqueTournament: dict[str, Any]
    roundInfo: NotRequired[dict[str, Any]]
    homeTeam: Team | None
    awayTeam: Team | None
    homeScore: Score | None
    awayScore: Score | None
    # tennis: players
    homePlayer: Player | None
    awayPlayer: Player | None
    winnerCode: NotRequired[int | None]


class StatItem(TypedDict, total=False):
    key: str
    name: NotRequired[str]
    value: float | int | None
    home: float | int | None
    away: float | int | None
    period: NotRequired[str]
    order: NotRequired[int]


class Statistics(TypedDict):
    eventId: int
    statistics: list[list[StatItem]] | list[StatItem]  # shape varies by sport
