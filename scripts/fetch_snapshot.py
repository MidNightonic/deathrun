#!/usr/bin/env python3
"""Fetch TitansDB guild data into member-centric JSON stores."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from guild_config import GuildConfig, load_guilds
from member_store import (
    GUILD_MEMBERS_FILENAME,
    MEMBERS_FILENAME,
    finalize_guild_member_store,
    finalize_member_store,
    load_guild_member_store,
    load_member_store,
    update_stores_from_snapshot,
    write_json,
)


API_URL = "https://www.titansdb.com/api/my_guild"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def request_json(api_key: str) -> tuple[int, Any]:
    query = urllib.parse.urlencode({"api_key": api_key})
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "X-API-Key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw": body[:2000]}
        return exc.code, payload
    except urllib.error.URLError as exc:
        return 0, {"error": "request_failed", "reason": str(exc.reason)}
    except json.JSONDecodeError as exc:
        return 0, {"error": "invalid_json", "reason": str(exc)}


def normalize_guild_stats(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    guild = payload.get("guild")
    if not isinstance(guild, dict):
        return {}
    return {
        "level": guild.get("level", ""),
        "population": guild.get("population", ""),
        "capacity": guild.get("capacity", ""),
        "networth": guild.get("networth", 0),
        "rank": guild.get("rank", ""),
    }


def normalize_members(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    guild = payload.get("guild")
    data = payload.get("data")
    candidates = [
        payload.get("members"),
        payload.get("players"),
        guild.get("members") if isinstance(guild, dict) else None,
        data.get("members") if isinstance(data, dict) else None,
        data.get("players") if isinstance(data, dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return candidate
    return []


def fetch_snapshot(guild: GuildConfig, api_key: str | None) -> dict[str, Any]:
    base = {
        "source": "api",
        "guild_slug": guild.slug,
        "guild_name": guild.name,
        "guild_id": guild.guild_id,
        "guild_url": guild.guild_url,
        "api_url": API_URL,
    }

    if not api_key:
        return {
            **base,
            "ok": False,
            "error": {
                "error": "missing_api_key",
                "reason": "Set TITANSDB_API_KEY in GitHub Actions repository secrets or local .env.",
            },
        }

    status, payload = request_json(api_key)
    if status != 200:
        return {**base, "ok": False, "http_status": status, "error": payload}

    return {
        **base,
        "ok": True,
        "members": normalize_members(payload),
        "guild_stats": normalize_guild_stats(payload),
        "raw": payload,
    }


def update_member_data(
    *,
    guild: GuildConfig,
    member_store: dict[str, Any],
    guild_store: dict[str, Any],
    snapshot_date: str,
    fetched_at: str,
    api_key: str | None,
) -> int:
    result = {
        "snapshot_date": snapshot_date,
        "fetched_at": fetched_at,
        **fetch_snapshot(guild, api_key),
    }

    if result.get("ok") is False:
        print(f"{guild.slug}: fetch failed ({result.get('error')}); aggregate data was not changed.")
        return 2

    updated = update_stores_from_snapshot(
        member_store=member_store,
        guild_store=guild_store,
        guild=guild,
        snapshot=result,
        snapshot_date=snapshot_date,
        fetched_at=fetched_at,
    )
    if not updated:
        print(f"{guild.slug}: no members found; aggregate data was not changed.")
        return 2

    print(f"{guild.slug}: updated {len(result.get('members') or [])} members for {snapshot_date}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--config", default="config/guilds.json")
    parser.add_argument("--guild-slug", default=None)
    parser.add_argument("--date", default=None, help="Override output date, format YYYY-MM-DD")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")

    config_path = (root / args.config).resolve()
    guilds = load_guilds(config_path)
    if args.guild_slug:
        guilds = [g for g in guilds if g.slug == args.guild_slug]
        if not guilds:
            raise SystemExit(f"Unknown guild slug: {args.guild_slug}")

    snapshot_date = args.date or dt.datetime.now().astimezone().date().isoformat()
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    output_dir = (root / args.output_dir).resolve()
    api_key = os.environ.get("TITANSDB_API_KEY", "").strip()
    members_path = output_dir / MEMBERS_FILENAME
    guild_members_path = output_dir / GUILD_MEMBERS_FILENAME
    member_store = load_member_store(members_path)
    guild_store = load_guild_member_store(guild_members_path)

    exit_code = 0
    changed = False
    for guild in guilds:
        result = update_member_data(
            guild=guild,
            member_store=member_store,
            guild_store=guild_store,
            snapshot_date=snapshot_date,
            fetched_at=fetched_at,
            api_key=api_key,
        )
        if result == 0:
            changed = True
        exit_code = max(exit_code, result)

    if changed:
        generated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        finalize_member_store(member_store, generated_at=generated_at)
        finalize_guild_member_store(guild_store, generated_at=generated_at)
        write_json(members_path, member_store)
        write_json(guild_members_path, guild_store)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
