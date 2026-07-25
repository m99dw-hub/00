"""
Zapis plikow kodu (wygenerowanych przez agenta developera) do lokalnego
klonu repo aplikacji Android.

Wydzielone od git_tools, zeby operacje na plikach (z walidacja sciezek)
byly oddzielone od operacji git (commit/tag/push).
"""
import os
from config import settings


def _safe_relpath(raw_path: str) -> str:
    """
    Zabezpieczenie przed path traversal (np. gdyby model zwrocil
    "../../etc/cron.d/x") - kod pochodzi z LLM, wiec nie ufamy mu domyslnie.
    """
    rel = os.path.normpath(raw_path.lstrip("/"))
    if rel.startswith("..") or os.path.isabs(rel):
        raise ValueError(f"Niedozwolona sciezka pliku od agenta developera: {raw_path!r}")
    return rel


def write_code_files(code_files: list[dict]) -> list[str]:
    """
    code_files: [{"path": "app/src/.../Foo.kt", "content": "..."}, ...]
    (dokladnie format, ktory zwraca agent developera - patrz prompts/system_prompts.py)

    Zapisuje kazdy plik pod ANDROID_REPO_PATH, tworzac brakujace katalogi.
    Zwraca liste zapisanych sciezek wzglednych (do przekazania do git add).
    """
    written: list[str] = []
    for entry in code_files:
        rel_path = _safe_relpath(entry["path"])
        abs_path = os.path.join(settings.ANDROID_REPO_PATH, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(entry["content"])
        written.append(rel_path)
    return written


def list_repo_tree(max_entries: int = 200) -> str:
    """
    Zwraca liste plikow istniejacych w repo aplikacji Android (wzgledne
    sciezki), pomijajac .git i katalogi budowania. Przekazywana agentom
    jako kontekst, zeby nie dzialali "w ciemno" bez wiedzy o istniejacym
    projekcie.
    """
    import os as _os

    ignored_dirs = {".git", "build", ".gradle", ".idea", "node_modules"}
    entries = []
    for root, dirs, files in _os.walk(settings.ANDROID_REPO_PATH):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for name in files:
            rel = _os.path.relpath(_os.path.join(root, name), settings.ANDROID_REPO_PATH)
            entries.append(rel)
            if len(entries) >= max_entries:
                return "\n".join(sorted(entries)) + "\n... (lista skrocona)"
    return "\n".join(sorted(entries)) if entries else "(repo puste)"
