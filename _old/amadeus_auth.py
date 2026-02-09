from __future__ import annotations

import os
import time
import httpx
from typing import Optional


class AmadeusTokenManager:
    def __init__(self, client_id: str, client_secret: str, base_url: str = "https://api.amadeus.com"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")

        self._token: Optional[str] = None
        self._expires_at: float = 0.0  # epoch seconds

    def _is_valid(self) -> bool:
        # margem de segurança de 30s
        return bool(self._token) and (time.time() < (self._expires_at - 30))

    async def get_token(self) -> str:
        if self._is_valid():
            return self._token  # type: ignore

        url = f"{self.base_url}/v1/security/oauth2/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, data=data)
            r.raise_for_status()
            payload = r.json()

        token = payload.get("access_token")
        expires_in = payload.get("expires_in", 0)

        if not token:
            raise RuntimeError(f"Amadeus token response sem access_token: {payload}")

        self._token = token
        self._expires_at = time.time() + float(expires_in or 0)
        return token


def build_token_manager_from_env() -> AmadeusTokenManager:
    client_id = (os.getenv("AMADEUS_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("AMADEUS_CLIENT_SECRET") or "").strip()
    base_url = (os.getenv("AMADEUS_BASE_URL") or "https://api.amadeus.com").strip()

    if not client_id or not client_secret:
        raise SystemExit("Defina AMADEUS_CLIENT_ID e AMADEUS_CLIENT_SECRET no .env")

    return AmadeusTokenManager(client_id=client_id, client_secret=client_secret, base_url=base_url)
