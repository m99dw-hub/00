"""
Centralna konfiguracja orchestratora.
Wszystko czytane ze zmiennych srodowiskowych (.env).
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # OpenRouter
    AI_API: str = os.environ["AI_API"]
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    COST_ALERT_THRESHOLD: float = float(os.getenv("COST_ALERT_THRESHOLD", "0.8"))

    # GitHub
    GITHUB_TOKEN: str = os.environ["GITHUB_TOKEN"]
    GITHUB_REPO: str = os.environ["GITHUB_REPO"]  # "user/repo"
    GITHUB_DEFAULT_BRANCH: str = os.getenv("GITHUB_DEFAULT_BRANCH", "main")

    # Siec wewnetrzna
    ORCHESTRATOR_HOST: str = os.getenv("ORCHESTRATOR_HOST", "127.0.0.1")
    ORCHESTRATOR_PORT: int = int(os.getenv("ORCHESTRATOR_PORT", "8000"))
    BRIDGE_HOST: str = os.getenv("BRIDGE_HOST", "127.0.0.1")
    BRIDGE_PORT: int = int(os.getenv("BRIDGE_PORT", "3000"))
    BRIDGE_SEND_URL: str = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}/send"

    # Bezpieczenstwo
    ALLOWED_WHATSAPP_JID: str = os.environ["ALLOWED_WHATSAPP_JID"]

    # Repo lokalne
    ANDROID_REPO_PATH: str = os.getenv("ANDROID_REPO_PATH", "/home/agent/android-app")
    REQUIREMENTS_FILE: str = os.path.join(ANDROID_REPO_PATH, "REQUIREMENTS.md")
    CHANGELOG_FILE: str = os.path.join(ANDROID_REPO_PATH, "CHANGELOG.md")

    # Petla weryfikacji
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))

    # Mapowanie agent -> model OpenRouter (dostosuj pod budzet/jakosc).
    # Sluggi zweryfikowane na openrouter.ai/models (lipiec 2026). Jesli wolisz,
    # zeby system sam sledzil najnowsza wersje danej rodziny, uzyj aliasu
    # "anthropic/claude-sonnet-latest" / "anthropic/claude-haiku-latest"
    # zamiast konkretnej wersji (kosztem przewidywalnosci przy zmianie modelu).
    AGENT_MODELS: dict = {
        "orchestrator": "anthropic/claude-sonnet-4.6",
        "requirements": "anthropic/claude-haiku-4.5",
        "ux": "anthropic/claude-haiku-4.5",
        "architect": "anthropic/claude-sonnet-4.6",
        "developer": "anthropic/claude-sonnet-4.6",
        "reviewer": "anthropic/claude-sonnet-4.6",
        "qa": "anthropic/claude-haiku-4.5",
        "devops": "anthropic/claude-haiku-4.5",
    }


settings = Settings()
