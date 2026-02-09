import asyncio
from datetime import datetime

from services.wikivoyage_cleaner import clean_payload
from services.itinerary_builder import build_day_by_day
from services.wikivoyage_client import WikivoyageClient

def parse_br(s: str):
    return datetime.strptime(s, "%d/%m/%Y").date()

async def main():
    client = WikivoyageClient()
    raw = await client.scrape_city("Barcelona")
    payload = clean_payload(raw)

    ida = parse_br("10/03/2026")
    volta = parse_br("15/03/2026")

    roteiro = build_day_by_day(payload["categories"], ida, volta, ritmo="médio")

    for d in roteiro:
        print(f"\nDia {d['dia']} - {d['data']}")
        for slot, text in d["atividades"]:
            print(" -", slot, text)

if __name__ == "__main__":
    asyncio.run(main())
