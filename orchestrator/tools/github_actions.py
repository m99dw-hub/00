"""
Wyzwalanie i odpytywanie GitHub Actions (testy instrumentalne/UI, ktore
nie mieszcza sie na Mikrusie - patrz android-app-template/.github/workflows/ci.yml).

Uzywane przez agenta QA (Review II) jako dodatkowe zrodlo prawdy obok
statycznej oceny LLM.
"""
import asyncio
import httpx
from config import settings

_API = "https://api.github.com"
_HEADERS = {
    "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}


async def trigger_ci(ref: str) -> None:
    """Odpala workflow_dispatch na danym branchu/commicie."""
    async with httpx.AsyncClient(headers=_HEADERS, timeout=30.0) as client:
        resp = await client.post(
            f"{_API}/repos/{settings.GITHUB_REPO}/actions/workflows/ci.yml/dispatches",
            json={"ref": ref},
        )
        resp.raise_for_status()


async def wait_for_ci_result(ref: str, timeout_s: int = 900, poll_every_s: int = 15) -> dict:
    """
    Czeka na zakonczenie najnowszego runu CI dla danego ref.
    Zwraca {"conclusion": "success"|"failure"|..., "url": "..."}.

    UWAGA: proste pollowanie po najnowszym runie - wystarczajace przy
    jednym zadaniu na raz (sekwencyjna kolejka, patrz architektura).
    """
    elapsed = 0
    async with httpx.AsyncClient(headers=_HEADERS, timeout=30.0) as client:
        while elapsed < timeout_s:
            resp = await client.get(
                f"{_API}/repos/{settings.GITHUB_REPO}/actions/workflows/ci.yml/runs",
                params={"branch": ref, "per_page": 1},
            )
            resp.raise_for_status()
            runs = resp.json().get("workflow_runs", [])
            if runs:
                run = runs[0]
                if run["status"] == "completed":
                    return {
                        "conclusion": run["conclusion"],
                        "url": run["html_url"],
                    }
            await asyncio.sleep(poll_every_s)
            elapsed += poll_every_s

    return {"conclusion": "timeout", "url": None}
