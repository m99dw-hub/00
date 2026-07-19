"""
Wspolny klient OpenRouter dla wszystkich agentow + licznik kosztow.

Kazde wywolanie LLM przechodzi przez ta funkcje, dzieki czemu mamy
jedno miejsce do: (a) wyboru modelu per rola, (b) zliczania tokenow/kosztu,
(c) sprawdzania limitu przed kosztownymi wywolaniami.
"""
import json

import httpx
from config import settings


class AgentJSONError(Exception):
    """Agent nie zwrocil poprawnego JSON-a mimo prob naprawy."""

_client = httpx.AsyncClient(
    base_url=settings.OPENROUTER_BASE_URL,
    headers={
        "Authorization": f"Bearer {settings.AI_API}",
        "Content-Type": "application/json",
    },
    timeout=120.0,
)

# Prosty licznik w pamieci procesu (uzupelniany danymi z odpowiedzi API).
# Do trwalego dziennika kosztow patrz tools/cost_monitor.py.
_session_usage_usd = 0.0


def get_session_usage_usd() -> float:
    return _session_usage_usd


async def call_agent(role: str, system_prompt: str, user_message: str) -> str:
    """
    Wywoluje model przypisany do danej roli agenta.
    role musi byc kluczem w settings.AGENT_MODELS (np. "developer", "reviewer").
    """
    global _session_usage_usd

    model = settings.AGENT_MODELS[role]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "usage": {"include": True},  # OpenRouter: dolacz koszt w odpowiedzi
    }

    resp = await _client.post("/chat/completions", json=payload)
    resp.raise_for_status()
    data = resp.json()

    cost = data.get("usage", {}).get("cost")
    if cost is not None:
        _session_usage_usd += float(cost)

    return data["choices"][0]["message"]["content"]


def strip_code_fence(text: str) -> str:
    """Usuwa markdown code fence (```json ... ```), jesli model go dodal."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


async def call_agent_json(
    role: str,
    system_prompt: str,
    user_message: str,
    max_repair_attempts: int = 2,
):
    """
    Jak call_agent, ale parsuje odpowiedz jako JSON. Mniejsze/tansze modele
    czasem dopisuja komentarz albo owijaja JSON w markdown fence - zamiast
    od razu wywalac cale zadanie, prosimy model o poprawienie formatu (do
    max_repair_attempts razy). Jesli nadal sie nie uda, podnosimy
    AgentJSONError - wywolujacy wezel w grafie decyduje, co z tym zrobic
    (zwykle: potraktowac jak nieudana probe i zwiekszyc retry_count, a nie
    ubijac cale zadanie).
    """
    response = await call_agent(role, system_prompt, user_message)
    last_response = response

    for attempt in range(max_repair_attempts + 1):
        try:
            return json.loads(strip_code_fence(last_response))
        except json.JSONDecodeError as exc:
            if attempt == max_repair_attempts:
                raise AgentJSONError(
                    f"Agent '{role}' nie zwrocil poprawnego JSON po "
                    f"{max_repair_attempts} probach naprawy: {exc}"
                ) from exc
            last_response = await call_agent(
                role, system_prompt,
                "Twoja poprzednia odpowiedz nie byla poprawnym JSON-em:\n"
                f"{last_response}\n\n"
                "Zwroc WYLACZNIE poprawny JSON, bez komentarzy, bez markdown "
                "code fence, bez dodatkowego tekstu przed ani po.",
            )
