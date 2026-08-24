from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from guild_config import GuildConfig


MEMBERS_FILENAME = "members.json"
GUILD_MEMBERS_FILENAME = "guild-members.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_member_store(path: Path) -> dict[str, Any]:
    payload = read_json_object(path)
    if not payload:
        payload = {}
    members = payload.get("members")
    if not isinstance(members, dict):
        members = {}
    return {
        "schema_version": 1,
        "generated_at": payload.get("generated_at") or utc_now(),
        "dates": payload.get("dates") if isinstance(payload.get("dates"), list) else [],
        "members": members,
    }


def load_guild_member_store(path: Path) -> dict[str, Any]:
    payload = read_json_object(path)
    if not payload:
        payload = {}
    guilds = payload.get("guilds")
    if not isinstance(guilds, dict):
        guilds = {}
    return {
        "schema_version": 1,
        "generated_at": payload.get("generated_at") or utc_now(),
        "guilds": guilds,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)


def get_members(snapshot: dict[str, Any]) -> list[Any]:
    if isinstance(snapshot.get("members"), list):
        return snapshot["members"]
    raw = snapshot.get("raw")
    if isinstance(raw, dict):
        guild = raw.get("guild")
        if isinstance(guild, dict) and isinstance(guild.get("members"), list):
            return guild["members"]
    return []


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def stat_of(member: dict[str, Any], key: str) -> int:
    stats = member.get("stats")
    value = stats.get(key, 0) if isinstance(stats, dict) else member.get(key, 0)
    return _int(value)


def investment_of(member: Any) -> int:
    if not isinstance(member, dict):
        return 0
    return stat_of(member, "investments")


def event_score_of(member: dict[str, Any]) -> int:
    events = member.get("events")
    if not isinstance(events, dict):
        return 0
    return _int(events.get("current_event_score"))


def role_of(member: dict[str, Any]) -> str:
    guild = member.get("guild")
    if isinstance(guild, dict):
        return str(guild.get("rank") or "")
    return str(member.get("rank") or "")


def joined_of(member: dict[str, Any]) -> str:
    guild = member.get("guild")
    if isinstance(guild, dict):
        return str(guild.get("joined") or "")
    return ""


def normalize_member_id(member: dict[str, Any]) -> str:
    return str(member.get("id") or member.get("name") or "").strip()


def should_replace_latest(existing_date: Any, snapshot_date: str) -> bool:
    return not existing_date or str(existing_date) <= snapshot_date


def finalize_member_store(member_store: dict[str, Any], generated_at: str | None = None) -> None:
    dates: set[str] = set()
    members = member_store.setdefault("members", {})
    if not isinstance(members, dict):
        member_store["members"] = {}
        members = member_store["members"]

    for record in members.values():
        if not isinstance(record, dict):
            continue
        investments = record.get("investments")
        if not isinstance(investments, dict):
            investments = {}
        dates.update(str(date) for date in investments)
        record["investments"] = {date: investments[date] for date in sorted(investments)}

        event_scores = record.get("event_scores")
        if not isinstance(event_scores, dict):
            event_scores = {}
        record["event_scores"] = {date: event_scores[date] for date in sorted(event_scores)}

        networth_history = record.get("networth_history")
        if not isinstance(networth_history, dict):
            networth_history = {}
        record["networth_history"] = {date: networth_history[date] for date in sorted(networth_history)}

        bounty_history = record.get("bounty_history")
        if not isinstance(bounty_history, dict):
            bounty_history = {}
        record["bounty_history"] = {date: bounty_history[date] for date in sorted(bounty_history)}

    member_store["schema_version"] = 1
    member_store["generated_at"] = generated_at or utc_now()
    member_store["dates"] = sorted(dates)


def finalize_guild_member_store(guild_store: dict[str, Any], generated_at: str | None = None) -> None:
    guild_store["schema_version"] = 1
    guild_store["generated_at"] = generated_at or utc_now()


def last_change_date(investments: dict[str, int], as_of_date: str) -> str | None:
    """Most recent date (<= as_of_date) where the investment total differs from the
    prior recorded value. If it never changes, returns the earliest recorded date."""
    dates = sorted(d for d in investments if d <= as_of_date)
    if not dates:
        return None
    last_change = dates[0]
    for prev, curr in zip(dates, dates[1:]):
        if investments[curr] != investments[prev]:
            last_change = curr
    return last_change


def inactivity_days(investments: dict[str, int], as_of_date: str) -> int | None:
    """Days since the member's investment total last increased, as of as_of_date."""
    changed_on = last_change_date(investments, as_of_date)
    if changed_on is None:
        return None
    d1 = dt.date.fromisoformat(changed_on)
    d2 = dt.date.fromisoformat(as_of_date)
    return (d2 - d1).days


def update_stores_from_snapshot(
    *,
    member_store: dict[str, Any],
    guild_store: dict[str, Any],
    guild: GuildConfig,
    snapshot: dict[str, Any],
    snapshot_date: str,
    fetched_at: str | None,
) -> bool:
    if snapshot.get("ok") is False:
        return False

    members = get_members(snapshot)
    if not members:
        return False

    guild_stats = snapshot.get("guild_stats")
    if not isinstance(guild_stats, dict):
        guild_stats = {}

    member_records = member_store.setdefault("members", {})
    guild_members_map: dict[str, dict[str, str]] = {}

    for member in members:
        if not isinstance(member, dict):
            continue
        member_id = normalize_member_id(member)
        if not member_id:
            continue

        name = str(member.get("name") or member_id)
        investment = investment_of(member)
        event_score = event_score_of(member)
        networth = stat_of(member, "networth")
        bounty = stat_of(member, "bounty")
        record = member_records.setdefault(
            member_id,
            {
                "name": name,
                "level": "",
                "role": "",
                "latest_investment": 0,
                "latest_date": "",
                "investments": {},
                "event_scores": {},
                "networth_history": {},
                "bounty_history": {},
            },
        )

        def _set_history(field: str, value: int) -> None:
            history = record.setdefault(field, {})
            if not isinstance(history, dict):
                history = {}
                record[field] = history
            history[snapshot_date] = value

        _set_history("investments", investment)
        _set_history("event_scores", event_score)
        _set_history("networth_history", networth)
        _set_history("bounty_history", bounty)

        if should_replace_latest(record.get("latest_date"), snapshot_date):
            events = member.get("events") if isinstance(member.get("events"), dict) else {}
            record["name"] = name
            record["level"] = member.get("level") or ""
            record["role"] = role_of(member)
            record["joined"] = joined_of(member)
            record["is_vip"] = bool(member.get("isVip"))
            record["last_online"] = str(member.get("last_online") or "")
            record["latest_investment"] = investment
            record["latest_date"] = snapshot_date
            record["latest_networth"] = stat_of(member, "networth")
            record["latest_help"] = stat_of(member, "help")
            record["latest_bounty"] = stat_of(member, "bounty")
            record["latest_prestige"] = stat_of(member, "prestige")
            record["latest_hero_power"] = stat_of(member, "best_hero_power")
            record["latest_event_score"] = event_score
            record["best_lcog_score"] = _int(events.get("best_lcog_score"))
            record["best_kc_score"] = _int(events.get("best_kc_score"))
            record["best_di_score"] = _int(events.get("best_di_score"))

        guild_members_map[member_id] = {"id": member_id, "name": name}

    guild_members = list(guild_members_map.values())
    if not guild_members:
        return False

    guilds = guild_store.setdefault("guilds", {})
    existing = guilds.get(guild.slug)
    existing_date = existing.get("snapshot_date") if isinstance(existing, dict) else ""
    if should_replace_latest(existing_date, snapshot_date):
        guilds[guild.slug] = {
            "slug": guild.slug,
            "name": guild.name,
            "guild_id": guild.guild_id,
            "guild_url": guild.guild_url,
            "snapshot_date": snapshot_date,
            "fetched_at": fetched_at or "",
            "level": guild_stats.get("level", ""),
            "population": guild_stats.get("population", ""),
            "capacity": guild_stats.get("capacity", ""),
            "networth": guild_stats.get("networth", 0),
            "rank": guild_stats.get("rank", ""),
            "members": guild_members,
        }

    return True
