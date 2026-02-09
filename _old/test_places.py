import asyncio
import os
from datetime import datetime
from _old.amadeus_places import AmadeusPlaces
from services.trip_logic import build_day_by_day_itinerary

def parse_br(s: str):
    return datetime.strptime(s, "%d/%m/%Y").date()

async def main():
    token = os.getenv("AMADEUS_ACCESS_TOKEN")
    if not token:
        raise SystemExit("Defina AMADEUS_ACCESS_TOKEN no .env (ou use seu token manager).")

    destino = "Barcelona"  # teste com qualquer cidade do mundo
    ida = parse_br("10/03/2026")
    volta = parse_br("15/03/2026")
    estilos = ["rock", "restaurantes"]

    places = AmadeusPlaces(token)
    pois = await places.pois_by_city(destino, radius_km=8, limit=30)

    roteiro = build_day_by_day_itinerary(destino, pois, estilos, "médio", ida, volta)
    for d in roteiro:
        print(d["dia"], d["data"])
        for slot, act in d["atividades"]:
            print(" -", slot, act)

if __name__ == "__main__":
    asyncio.run(main())
