from __future__ import annotations

import math
from typing import Any

from sport_scraper.schemas.core import (
    Event,
    EventStatus,
    Player,
    Score,
    SportSlug,
    StatItem,
    Statistics,
    StatusType,
    Team,
)


# Map SofaScore status.type values to our stable enum-like set
STATUS_MAP = {
    "notstarted": "notstarted",
    "inprogress": "inprogress",
    "finished": "finished",
    "postponed": "postponed",
    "cancelled": "cancelled",
    "suspended": "suspended",
    "interrupted": "interrupted",
    "abandoned": "abandoned",
    "retired": "retired",
    "walkover": "walkover",
    "deducted": "deducted",
    "lostbywalkover": "lostbywalkover",
    "live": "inprogress",
}


def to_status_type(stype: str | None) -> StatusType:
    if not stype:
        return "notstarted"
    return STATUS_MAP.get(stype, "notstarted")  # type: ignore[return-value]


def to_team(t: dict[str, Any] | None) -> Team | None:
    if not t:
        return None
    return {
        "id": t.get("id"),
        "name": t.get("name") or t.get("shortName") or "",
        "slug": t.get("slug"),
        "shortName": t.get("shortName"),
        "country": t.get("country") or (t.get("teamCategory") or {}).get("name"),
        "flag": t.get("flag") or (t.get("teamCategory") or {}).get("flag"),
        "isWinner": t.get("isWinner"),
    }  # type: ignore[dict-item]


def to_player(p: dict[str, Any] | None) -> Player | None:
    if not p:
        return None
    return {
        "id": p.get("id"),
        "name": p.get("name") or f"{p.get('firstName') or ''} {p.get('lastName') or ''}".strip(),
        "firstName": p.get("firstName"),
        "lastName": p.get("lastName"),
        "shortName": p.get("shortName"),
        "country": p.get("country") or (p.get("countryCode")),
        "isWinner": p.get("isWinner"),
    }  # type: ignore[dict-item]


def to_score(s: dict[str, Any] | None) -> Score | None:
    if not s:
        return None
    out: Score = {}
    for k in (
        "current",
        "period1",
        "period2",
        "period3",
        "period4",
        "overtime",
        "innings",
        "runs",
        "wickets",
        "overs",
        "sets",
        "games",
        "points",
    ):
        if k in s and s[k] is not None:
            v = s[k]
            if k == "overs" and isinstance(v, (int, float)) and math.isfinite(v):
                out[k] = float(v)  # type: ignore[literal-required]
            elif isinstance(v, (int, float)):
                out[k] = v  # type: ignore[literal-required]
    return out


def normalize_event(e: dict[str, Any], sport: SportSlug | None = None) -> Event:
    stype = to_status_type((e.get("status") or {}).get("type"))
    status: EventStatus = {
        "type": stype,
        "description": (e.get("status") or {}).get("description"),
        "code": (e.get("status") or {}).get("code"),
    }
    sport_slug = sport or (e.get("tournament") or {}).get("category", {}).get("sport", {}).get("slug") or e.get("sport", {}).get("slug")
    if sport_slug == "american-football":
        sport_slug = "american-football"
    return {
        "id": e["id"],
        "uid": e.get("customId") or e.get("uid"),
        "name": e.get("name"),
        "slug": e.get("slug"),
        "sport": sport_slug,  # type: ignore[typeddict-item]
        "startTimestamp": e.get("startTimestamp") or e.get("startDateTimestamp") or 0,
        "startDateTime": e.get("startDate") or e.get("formatedStartDate") or e.get("startTime"),
        "status": status,
        "tournament": e.get("tournament") or {},
        "uniqueTournament": e.get("uniqueTournament") or (e.get("tournament") or {}).get("uniqueTournament") or {},
        "roundInfo": e.get("roundInfo"),
        "homeTeam": to_team(e.get("homeTeam")),
        "awayTeam": to_team(e.get("awayTeam")),
        "homePlayer": to_player(e.get("homePlayer")),
        "awayPlayer": to_player(e.get("awayPlayer")),
        "homeScore": to_score(e.get("homeScore")),
        "awayScore": to_score(e.get("awayScore")),
        "winnerCode": e.get("winnerCode"),
    }


def normalize_statistics(data: dict[str, Any]) -> Statistics:
    stats = data.get("statistics") or []
    # SofaScore returns various shapes; normalize to list of lists or list
    return {
        "eventId": data.get("eventId") or data.get("id") or 0,
        "statistics": stats,
    }
