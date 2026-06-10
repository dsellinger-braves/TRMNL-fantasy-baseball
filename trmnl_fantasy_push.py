#!/usr/bin/env python3
"""
Fetch roto standings from a public ESPN fantasy baseball league and push
them to a TRMNL private plugin webhook.

Usage:
    TRMNL_WEBHOOK_URL=https://usetrmnl.com/api/custom_plugins/XXXX \
        python trmnl_fantasy_push.py

Requires only `requests`.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

import requests

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
LEAGUE_ID = os.environ.get("LEAGUE_ID") or "130215"
SEASON = int(os.environ.get("SEASON") or datetime.now().year)
TRMNL_WEBHOOK_URL = (
    os.environ.get("TRMNL_WEBHOOK_URL")
    or "https://usetrmnl.com/api/custom_plugins/YOUR_PLUGIN_UUID"
)
MAX_STANDINGS_ROWS = 12
NAME_MAX_LEN = 18

ESPN_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/"
    f"seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (TRMNL fantasy plugin)",
    "Accept": "application/json",
}

# ESPN MLB stat IDs -> labels for the common roto categories.
# Unmapped IDs render as "STAT_<id>" -- rename here if one shows up.
STAT_NAMES = {
    2: "AVG", 5: "HR", 17: "OBP", 18: "OPS", 20: "R", 21: "RBI", 23: "SB",
    41: "WHIP", 47: "ERA", 48: "K", 53: "W", 57: "SV", 58: "SVHD",
    63: "QS", 83: "SVHD",
}
# Stats formatted as rate stats rather than counting numbers
THREE_DECIMAL = {2, 17, 18}   # AVG / OBP / OPS -> .302
TWO_DECIMAL = {41, 47}        # WHIP / ERA -> 3.41


def fetch_league() -> dict:
    params = [
        ("view", "mTeam"),
        ("view", "mStandings"),
        ("view", "mSettings"),
    ]
    resp = requests.get(ESPN_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def team_name(team: dict) -> str:
    name = team.get("name") or " ".join(
        p for p in (team.get("location"), team.get("nickname")) if p
    )
    name = (name or team.get("abbrev", "???")).strip()
    return name[:NAME_MAX_LEN]


def fmt_points(pts: float) -> str:
    """Roto points: drop trailing .0 but keep halves (ties split points)."""
    return f"{pts:g}"


def fmt_stat(stat_id: int, value: float) -> str:
    if stat_id in THREE_DECIMAL:
        return f"{value:.3f}".lstrip("0")
    if stat_id in TWO_DECIMAL:
        return f"{value:.2f}"
    return f"{value:g}"


def build_standings(data: dict) -> list[dict]:
    rows = []
    for t in data.get("teams", []):
        rows.append(
            {
                "name": team_name(t),
                "abbrev": t.get("abbrev", "")[:4],
                "pts": t.get("points", 0.0),
                "seed": t.get("playoffSeed") or 99,
            }
        )
    # playoffSeed mirrors ESPN's standings page; fall back to points
    rows.sort(key=lambda r: (r["seed"], -r["pts"]))

    leader_pts = rows[0]["pts"] if rows else 0
    for r in rows:
        back = leader_pts - r["pts"]
        r["back"] = "-" if back <= 0 else fmt_points(back)
        r["pts"] = fmt_points(r["pts"])
        del r["seed"]

    return rows[:MAX_STANDINGS_ROWS]


def get_scoring_stat_ids(data: dict) -> list[tuple[int, bool]]:
    """Return [(stat_id, lower_is_better), ...] for the league's categories."""
    items = (
        data.get("settings", {})
        .get("scoringSettings", {})
        .get("scoringItems", [])
    )
    out = []
    for item in items:
        sid = item.get("statId")
        if sid is not None:
            out.append((sid, bool(item.get("isReverseItem"))))
    return out


def build_category_leaders(data: dict) -> list[dict]:
    """Who leads each scoring category, by raw stat value."""
    stat_ids = get_scoring_stat_ids(data)
    teams = data.get("teams", [])
    leaders = []

    for sid, reverse in stat_ids:
        best_team, best_val = None, None
        for t in teams:
            vals = t.get("valuesByStat") or {}
            # JSON keys are strings
            v = vals.get(str(sid), vals.get(sid))
            if v is None:
                continue
            if (
                best_val is None
                or (reverse and v < best_val)
                or (not reverse and v > best_val)
            ):
                best_team, best_val = t, v
        if best_team is None:
            continue
        leaders.append(
            {
                "cat": STAT_NAMES.get(sid, f"STAT_{sid}"),
                "abbrev": best_team.get("abbrev", "???")[:4],
                "val": fmt_stat(sid, best_val),
            }
        )
    return leaders


def build_payload(data: dict) -> dict:
    settings = data.get("settings", {})
    now_et = datetime.now(timezone.utc) - timedelta(hours=4)

    merge_variables = {
        "league_name": settings.get("name", "Fantasy Baseball")[:30],
        "updated": now_et.strftime("%b %-d, %-I:%M %p"),
        "standings": build_standings(data),
        "leaders": build_category_leaders(data),
    }
    return {"merge_variables": merge_variables}


def push_to_trmnl(payload: dict) -> None:
    body = json.dumps(payload, separators=(",", ":"))
    size = len(body.encode())
    print(f"Payload size: {size} bytes")

    if size > 2000:  # TRMNL webhook limit is 2KB
        for row in payload["merge_variables"]["standings"]:
            row["name"] = row["name"][:12]
        body = json.dumps(payload, separators=(",", ":"))
        print(f"Trimmed payload to {len(body.encode())} bytes")

    resp = requests.post(
        TRMNL_WEBHOOK_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    print(f"TRMNL response: {resp.status_code} {resp.text[:200]}")
    resp.raise_for_status()


def main() -> int:
    if "YOUR_PLUGIN_UUID" in TRMNL_WEBHOOK_URL:
        print("Set TRMNL_WEBHOOK_URL (env var or edit the script).")
        return 1
    data = fetch_league()
    payload = build_payload(data)
    print(json.dumps(payload, indent=2))
    push_to_trmnl(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
