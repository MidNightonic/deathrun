#!/usr/bin/env python3
"""Build README and per-guild reports, including inactivity flags."""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from guild_config import GuildConfig, load_guilds
from member_store import (
    GUILD_MEMBERS_FILENAME,
    MEMBERS_FILENAME,
    inactivity_days,
    read_json_object,
)

HUNDRED_MILLION = 100_000_000
INACTIVE_THRESHOLD_DAYS = 7  # flag members whose investment hasn't moved in this many days


@dataclass
class MemberRecord:
    id: str
    latest_name: str
    latest_level: Any = ""
    latest_role: str = ""
    by_date: dict[str, int] = field(default_factory=dict)


@dataclass
class GuildSummary:
    guild: GuildConfig
    report_path: Path
    updated_at: str
    valid_count: int
    days: int
    latest_date: str
    latest_member_count: int
    latest_total: int
    inactive_count: int


def markdown_escape(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def format_yi(value: int | float | None) -> str:
    if value is None:
        return "-"
    return f"{value / HUNDRED_MILLION:.2f}亿"


def format_delta(value: int | float | None) -> str:
    if value is None:
        return ""
    sign = "+" if value > 0 else ""
    return f"{sign}{value / HUNDRED_MILLION:.2f}亿"


def load_required_json(path: Path) -> dict[str, Any]:
    payload = read_json_object(path)
    if payload is None:
        raise FileNotFoundError(f"Missing or invalid JSON: {path}")
    return payload


def numeric_investments(record: dict[str, Any]) -> dict[str, int]:
    investments = record.get("investments")
    if not isinstance(investments, dict):
        return {}
    values: dict[str, int] = {}
    for date, value in investments.items():
        try:
            values[str(date)] = int(value)
        except (TypeError, ValueError):
            continue
    return values


def build_model(
    guild_entry: dict[str, Any],
    member_store: dict[str, Any],
    days: int,
) -> tuple[list[str], list[MemberRecord], int]:
    all_members = member_store.get("members")
    if not isinstance(all_members, dict):
        all_members = {}

    raw_guild_members = guild_entry.get("members")
    guild_members = raw_guild_members if isinstance(raw_guild_members, list) else []
    dates: set[str] = set()
    records: list[MemberRecord] = []

    for item in guild_members:
        if not isinstance(item, dict):
            continue
        member_id = str(item.get("id") or "").strip()
        if not member_id:
            continue
        stored = all_members.get(member_id)
        if not isinstance(stored, dict):
            continue
        by_date = numeric_investments(stored)
        dates.update(by_date)
        records.append(
            MemberRecord(
                id=member_id,
                latest_name=str(item.get("name") or stored.get("name") or member_id),
                latest_level=stored.get("level") or "",
                latest_role=str(stored.get("role") or ""),
                by_date=by_date,
            )
        )

    recent_dates = sorted(dates)[-days:]
    recent_date_set = set(recent_dates)
    for record in records:
        record.by_date = {d: v for d, v in record.by_date.items() if d in recent_date_set}

    latest_date = recent_dates[-1] if recent_dates else ""
    ordered = sorted(records, key=lambda item: item.by_date.get(latest_date, 0), reverse=True)
    return recent_dates, ordered, len(guild_members)


def cell_value(member: MemberRecord, date: str, previous_date: str | None) -> str:
    current = member.by_date.get(date)
    if current is None:
        return "-"
    previous = member.by_date.get(previous_date) if previous_date else None
    if previous is None:
        return format_yi(current)
    return f"{format_yi(current)} ({format_delta(current - previous)})"


def top_investors(dates: list[str], members: list[MemberRecord], limit: int = 10) -> list[tuple[MemberRecord, int]]:
    if len(dates) < 2:
        return []
    first_date, latest_date = dates[0], dates[-1]
    gains: list[tuple[MemberRecord, int]] = []
    for member in members:
        end = member.by_date.get(latest_date)
        if end is None:
            continue
        start = member.by_date.get(first_date)
        if start is None:
            in_range = [d for d in sorted(member.by_date) if first_date <= d <= latest_date]
            if len(in_range) < 2:
                continue
            start = member.by_date[in_range[0]]
        gain = end - start
        if gain > 0:
            gains.append((member, gain))
    return sorted(gains, key=lambda item: item[1], reverse=True)[:limit]


def inactive_members(
    dates: list[str], members: list[MemberRecord], threshold_days: int
) -> list[tuple[MemberRecord, int]]:
    """Members whose investment total hasn't increased in >= threshold_days."""
    if not dates:
        return []
    latest_date = dates[-1]
    flagged: list[tuple[MemberRecord, int]] = []
    for member in members:
        idle = inactivity_days(member.by_date, latest_date)
        if idle is not None and idle >= threshold_days:
            flagged.append((member, idle))
    return sorted(flagged, key=lambda item: item[1], reverse=True)


def render_top_section(dates: list[str], members: list[MemberRecord]) -> str:
    lines = ["## Top New Investment (last window)", ""]
    ranking = top_investors(dates, members)
    if not ranking:
        lines.append("Need at least two days of investment data to compute this.")
        return "\n".join(lines)
    lines.extend(["| # | Member | Level | Role | New Investment |", "| ---: | --- | ---: | --- | ---: |"])
    for index, (member, gain) in enumerate(ranking, start=1):
        lines.append(
            f"| {index} | {markdown_escape(member.latest_name)} | {markdown_escape(member.latest_level)} | "
            f"{markdown_escape(member.latest_role)} | {format_delta(gain)} |"
        )
    return "\n".join(lines)


def render_inactive_section(dates: list[str], members: list[MemberRecord], threshold_days: int) -> str:
    lines = [f"## ⚠ Inactive Members (no investment change in {threshold_days}+ days)", ""]
    flagged = inactive_members(dates, members, threshold_days)
    if not flagged:
        lines.append("Nobody is currently flagged as inactive. 🎉")
        return "\n".join(lines)
    lines.extend(["| Member | Level | Role | Days Idle |", "| --- | ---: | --- | ---: |"])
    for member, idle in flagged:
        lines.append(
            f"| {markdown_escape(member.latest_name)} | {markdown_escape(member.latest_level)} | "
            f"{markdown_escape(member.latest_role)} | {idle} |"
        )
    return "\n".join(lines)


def render_detail_table(dates: list[str], members: list[MemberRecord]) -> str:
    display_dates = list(reversed(dates))
    previous_by_date = {date: dates[index - 1] if index > 0 else None for index, date in enumerate(dates)}
    lines = [
        "## Investment Detail",
        "",
        "Cell format: `total (change since previous record)`. Most recent date is on the left.",
        "",
        "| Member | Level | Role | " + " | ".join(display_dates) + " |",
        "| --- | ---: | --- | " + " | ".join(["---:"] * len(display_dates)) + " |",
    ]
    for member in members:
        values = [cell_value(member, date, previous_by_date[date]) for date in display_dates]
        lines.append(
            f"| {markdown_escape(member.latest_name)} | {markdown_escape(member.latest_level)} | "
            f"{markdown_escape(member.latest_role)} | " + " | ".join(markdown_escape(v) for v in values) + " |"
        )
    return "\n".join(lines)


def build_guild_report(
    guild: GuildConfig,
    guild_entry: dict[str, Any],
    member_store: dict[str, Any],
    report_path: Path,
    days: int,
    inactive_threshold: int,
) -> GuildSummary:
    dates, members, listed_member_count = build_model(guild_entry, member_store, days)
    latest_date = dates[-1] if dates else "-"
    latest_total = sum(member.by_date.get(latest_date, 0) for member in members) if dates else 0
    updated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    flagged = inactive_members(dates, members, inactive_threshold) if dates else []

    sections = [
        f"# {guild.name} — Guild Snapshot",
        "",
        "<!-- Generated by scripts/build_report.py. Do not edit tables manually. -->",
        "",
        f"- Guild page: {guild.guild_url}",
        f"- Guild ID: `{guild.guild_id}`",
        f"- Report updated: {updated_at}",
        f"- Valid investment dates: {len(dates)} / last {days} days",
        f"- Latest member list date: {guild_entry.get('snapshot_date') or '-'}",
        f"- Latest investment date: {latest_date}",
        f"- Latest member count: {listed_member_count}",
        f"- Latest total investment: {format_yi(latest_total)}",
        f"- Inactive members flagged: {len(flagged)}",
        "",
    ]

    if not dates:
        sections.extend(
            [
                "## No investment data yet",
                "",
                f"Run `python3 scripts/fetch_snapshot.py --guild-slug {guild.slug}` first.",
            ]
        )
    else:
        sections.extend(
            [
                render_inactive_section(dates, members, inactive_threshold),
                "",
                render_top_section(dates, members),
                "",
                render_detail_table(dates, members),
            ]
        )

    sections.extend(
        [
            "",
            "## Auto-update",
            "",
            "GitHub Actions runs `.github/workflows/daily-snapshot.yml` on a daily cron. "
            "You can also trigger it manually from the Actions tab.",
            "",
            f"Each run pulls the latest guild member list, updates each member's investment "
            f"history in `data/{MEMBERS_FILENAME}`, and refreshes `data/{GUILD_MEMBERS_FILENAME}`.",
            "",
        ]
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(sections), encoding="utf-8")

    return GuildSummary(
        guild=guild,
        report_path=report_path,
        updated_at=updated_at,
        valid_count=len(dates),
        days=days,
        latest_date=latest_date,
        latest_member_count=listed_member_count,
        latest_total=latest_total,
        inactive_count=len(flagged),
    )


def relative_markdown_path(from_path: Path, to_path: Path) -> str:
    return to_path.relative_to(from_path.parent).as_posix()


def build_root_readme(output_path: Path, summaries: list[GuildSummary]) -> None:
    updated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    lines = [
        "# Guild Investment & Activity Snapshots",
        "",
        "<!-- Generated by scripts/build_report.py. Do not edit tables manually. -->",
        "",
        f"- README updated: {updated_at}",
        f"- Guilds configured: {len(summaries)}",
        "- Web dashboard: enable GitHub Pages and open `index.html`.",
        "",
        "## Guild Reports",
        "",
        "| Guild | Guild ID | Latest Date | Members | Total Investment | Inactive | Report |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for summary in summaries:
        report_link = relative_markdown_path(output_path, summary.report_path)
        lines.append(
            f"| {markdown_escape(summary.guild.name)} | `{summary.guild.guild_id}` | {summary.latest_date} | "
            f"{summary.latest_member_count} | {format_yi(summary.latest_total)} | {summary.inactive_count} | "
            f"[view]({report_link}) |"
        )
    lines.extend(
        [
            "",
            "## Auto-update",
            "",
            "GitHub Actions runs `.github/workflows/daily-snapshot.yml` on a daily cron; "
            "trigger manually from the Actions tab if needed.",
            "",
            f"Guild list lives in `config/guilds.json`. Investment history is in `data/{MEMBERS_FILENAME}`; "
            f"latest membership is in `data/{GUILD_MEMBERS_FILENAME}`; per-guild reports go to `reports/`.",
            "",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def build_reports(
    config_path: Path, data_dir: Path, reports_dir: Path, output_path: Path, days: int, inactive_threshold: int
) -> None:
    guilds = load_guilds(config_path)
    member_store = load_required_json(data_dir / MEMBERS_FILENAME)
    guild_store = load_required_json(data_dir / GUILD_MEMBERS_FILENAME)
    guild_entries = guild_store.get("guilds") if isinstance(guild_store.get("guilds"), dict) else {}

    summaries = [
        build_guild_report(
            guild=guild,
            guild_entry=guild_entries.get(guild.slug, {}) if isinstance(guild_entries, dict) else {},
            member_store=member_store,
            report_path=reports_dir / f"{guild.slug}.md",
            days=days,
            inactive_threshold=inactive_threshold,
        )
        for guild in guilds
    ]
    build_root_readme(output_path, summaries)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/guilds.json")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--output", default="reports/SUMMARY.md")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--inactive-threshold", type=int, default=INACTIVE_THRESHOLD_DAYS)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    build_reports(
        config_path=(root / args.config).resolve(),
        data_dir=(root / args.data_dir).resolve(),
        reports_dir=(root / args.reports_dir).resolve(),
        output_path=(root / args.output).resolve(),
        days=args.days,
        inactive_threshold=args.inactive_threshold,
    )
    print((root / args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
