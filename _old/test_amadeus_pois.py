import asyncio
from services.amadeus_auth import build_token_manager_from_env
from services.amadeus_places import AmadeusPlaces

async def main():
    tm = build_token_manager_from_env()
    places = AmadeusPlaces(tm)

    pois = await places.pois_by_city("Barcelona", radius_km=8, limit=15)
    for p in pois:
        print("-", p["name"], "|", p.get("category"), "|", p.get("rank"))

if __name__ == "__main__":
    asyncio.run(main())
