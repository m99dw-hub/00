"""
Monitoring wydatkow OpenRoutera na poziomie konta/klucza.

Dziala niezaleznie od licznika w agents/base.py (ktory liczy tylko biezaca
sesje) - to pyta samo OpenRouter o realny stan klucza (limit miesieczny,
ile juz wydano w tym miesiacu) i wysyla ostrzezenie na WhatsApp, gdy
przekroczony zostanie prog COST_ALERT_THRESHOLD.

Uruchamiaj okresowo (np. co godzine) jako osobne zadanie w tle -
patrz main.py: @app.on_event("startup") + asyncio background task.
"""
import httpx
from config import settings

_last_alert_sent_at_pct: float = 0.0


async def check_and_alert(send_whatsapp_message) -> None:
    """
    send_whatsapp_message: async callable(chat_id: str, text: str)
    Wywolywane z jidem admina (ALLOWED_WHATSAPP_JID).
    """
    global _last_alert_sent_at_pct

    async with httpx.AsyncClient(
        base_url=settings.OPENROUTER_BASE_URL,
        headers={"Authorization": f"Bearer {settings.AI_API}"},
        timeout=30.0,
    ) as client:
        resp = await client.get("/key")
        resp.raise_for_status()
        data = resp.json()["data"]

    limit = data.get("limit")  # None = brak limitu ustawionego na kluczu
    usage_monthly = data.get("usage_monthly", 0.0)

    if limit is None or limit == 0:
        return  # brak sensu liczyc procent bez limitu

    pct = usage_monthly / limit

    # Wysylaj alert tylko raz na kazde przekroczone 10 punktow procentowych,
    # zeby nie spamowac WhatsAppa przy kazdym sprawdzeniu.
    if pct >= settings.COST_ALERT_THRESHOLD and pct - _last_alert_sent_at_pct >= 0.1:
        _last_alert_sent_at_pct = pct
        await send_whatsapp_message(
            settings.ALLOWED_WHATSAPP_JID,
            f"⚠️ Uwaga: wykorzystano {pct*100:.0f}% miesięcznego limitu "
            f"OpenRouter ({usage_monthly:.2f} / {limit:.2f} USD).",
        )

    if pct >= 1.0:
        await send_whatsapp_message(
            settings.ALLOWED_WHATSAPP_JID,
            "🛑 Miesięczny limit OpenRouter został wyczerpany. "
            "Dalsze zadania będą odrzucane przez OpenRouter, dopóki nie "
            "podniesiesz limitu lub nie nadejdzie reset.",
        )
