# services/wikivoyage_client.py
from __future__ import annotations

import time
import httpx
from typing import Dict, Any

API = "https://en.wikivoyage.org/w/api.php"

class WikivoyageClient:
    def __init__(self, user_agent: str = "TravelPlannerBot/0.1"):
        self.user_agent = user_agent

    async def _get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"User-Agent": self.user_agent}
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            r = await client.get(API, params=params)
            r.raise_for_status()
            return r.json()

    async def find_section_indexes(self, title: str) -> Dict[str, str]:
        data = await self._get({
            "action": "parse",
            "format": "json",
            "page": title,
            "prop": "sections",
        })

        sections = data.get("parse", {}).get("sections", []) or []
        want = {"see": None, "do": None, "eat": None, "drink": None}

        for s in sections:
            line = (s.get("line") or "").strip().lower()
            idx = s.get("index")

            # pega também "see and do", etc.
            if "see" in line and want["see"] is None:
                want["see"] = idx
            if "do" in line and want["do"] is None:
                want["do"] = idx
            if "eat" in line and want["eat"] is None:
                want["eat"] = idx
            if "drink" in line and want["drink"] is None:
                want["drink"] = idx

        return {k: v for k, v in want.items() if v is not None}

    async def fetch_section_html(self, title: str, section_index: str) -> str:
        data = await self._get({
            "action": "parse",
            "format": "json",
            "page": title,
            "prop": "text",
            "section": section_index,
        })
        return data.get("parse", {}).get("text", {}).get("*", "") or ""

    async def scrape_city(self, title: str) -> Dict[str, Any]:
        idxs = await self.find_section_indexes(title)

        categories: Dict[str, str] = {}
        for key, idx in idxs.items():
            html = await self.fetch_section_html(title, idx)
            categories[key] = html

        return {
            "source": "wikivoyage_mediawiki_api",
            "title": title,
            "url": f"https://en.wikivoyage.org/wiki/{title.replace(' ', '_')}",
            "sections_html": categories,  # see/do/eat/drink -> html
        }
