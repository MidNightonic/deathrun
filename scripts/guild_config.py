from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class GuildConfig:
    slug: str
    name: str
    guild_id: str

    @property
    def guild_url(self) -> str:
        if not self.guild_id:
            return "https://www.titansdb.com/"
        return f"https://www.titansdb.com/guilds/{self.guild_id}"


def load_guilds(config_path: Path) -> list[GuildConfig]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    raw_guilds: Any = payload.get("guilds") if isinstance(payload, dict) else None
    if not isinstance(raw_guilds, list):
        raise ValueError(f"{config_path} must contain a guilds list")

    guilds: list[GuildConfig] = []
    seen_slugs: set[str] = set()
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_guilds, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"guilds[{index}] must be an object")

        slug = str(item.get("slug") or "").strip()
        name = str(item.get("name") or "").strip()
        guild_id = str(item.get("guild_id") or "").strip()

        if not slug or not SLUG_RE.match(slug):
            raise ValueError(f"guilds[{index}].slug must use lowercase letters, numbers, and hyphens")
        if not name:
            raise ValueError(f"guilds[{index}].name is required")
        if slug in seen_slugs:
            raise ValueError(f"Duplicate guild slug: {slug}")
        if guild_id and guild_id in seen_ids:
            raise ValueError(f"Duplicate guild_id: {guild_id}")

        seen_slugs.add(slug)
        if guild_id:
            seen_ids.add(guild_id)
        guilds.append(GuildConfig(slug=slug, name=name, guild_id=guild_id))

    return guilds
