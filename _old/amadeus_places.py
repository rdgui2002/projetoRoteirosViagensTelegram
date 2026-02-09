from __future__ import annotations

import httpx
from typing import Any, Dict, List

from services.amadeus_auth import AmadeusTokenManager


class AmadeusPlaces:
    def __init__(self, token_manager: AmadeusTokenManager):
        self.tm = token_manager
        self.base = token_manager.base_url.rstrip("/")

    async def _headers(self) -> Dict[str, str]:
        token = await self.tm.get_token()
        return {"Authorization": f"Bearer {token}"}

    async def city_search(self, keyword: str) -> Dict[str, Any]:
        url = f"{self.base}/v1/reference-data/locations"
        params = {"subType": "CITY", "keyword": keyword, "page[limit]": 5}

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=await self._headers(), params=params)
            r.raise_for_status()
            data = r.json()

        if not data.get("data"):
            raise ValueError(f"Nenhuma cidade encontrada para: {keyword}")

        return data["data"][0]

    async def pois_by_city(self, city_name: str, radius_km: int = 8, limit: int = 25) -> List[Dict[str, Any]]:
        city = await self.city_search(city_name)
        geo = city.get("geoCode") or {}
        lat, lon = geo.get("latitude"), geo.get("longitude")
        if lat is None or lon is None:
            raise ValueError("Cidade sem geoCode retornado pela API.")

        url = f"{self.base}/v1/reference-data/locations/pois"
        params = {"latitude": lat, "longitude": lon, "radius": radius_km}

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=await self._headers(), params=params)
            r.raise_for_status()
            data = r.json()

        pois = (data.get("data") or [])[:limit]

        out = []
        for p in pois:
            out.append({
                "name": p.get("name"),
                "category": p.get("category"),
                "tags": p.get("tags", []),
                "geo": p.get("geoCode") or {},
                "rank": p.get("rank"),
            })
        return out
