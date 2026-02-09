import httpx
from bs4 import BeautifulSoup

async def enrich_city_with_wikipedia(city: str) -> dict:
    url = f"https://pt.wikipedia.org/wiki/{city.replace(' ', '_')}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url)
        r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    beaches = []
    for li in soup.select("li"):
        txt = li.get_text(strip=True)
        if any(k in txt.lower() for k in ["praia", "beach"]):
            beaches.append(txt)

    landmarks = []
    for b in soup.select("b"):
        t = b.get_text(strip=True)
        if len(t) > 3 and t.lower() not in city.lower():
            landmarks.append(t)

    return {
        "beaches": list(dict.fromkeys(beaches))[:10],
        "landmarks": list(dict.fromkeys(landmarks))[:15],
    }
