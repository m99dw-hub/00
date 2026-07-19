"""
Operacje git na lokalnym klonie repo aplikacji Android
(settings.ANDROID_REPO_PATH). Uzywane przez agenta DevOps do
commitowania, tagowania (semver) i pchania zmian.
"""
import re
from git import Repo
from config import settings
from tools import fs_tools


def _repo() -> Repo:
    return Repo(settings.ANDROID_REPO_PATH)


def get_latest_version() -> tuple[int, int, int]:
    repo = _repo()
    tags = sorted(
        (t.name for t in repo.tags if re.match(r"^v\d+\.\d+\.\d+$", t.name)),
        key=lambda v: [int(x) for x in v.lstrip("v").split(".")],
    )
    if not tags:
        return (0, 0, 0)
    major, minor, patch = (int(x) for x in tags[-1].lstrip("v").split("."))
    return (major, minor, patch)


def bump_version(kind: str) -> str:
    major, minor, patch = get_latest_version()
    if kind == "major":
        major, minor, patch = major + 1, 0, 0
    elif kind == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"v{major}.{minor}.{patch}"


def commit_and_tag(files: list[str], message: str, version_bump: str, branch: str) -> str:
    """
    Commituje wskazane pliki, taguje nowa wersja, pcha branch + tag.
    Zwraca nowy tag.
    """
    repo = _repo()
    repo.git.checkout(branch)
    repo.index.add(files)
    repo.index.commit(message)

    new_tag = bump_version(version_bump)
    repo.create_tag(new_tag)

    origin = repo.remote("origin")
    origin.push(branch)
    origin.push(new_tag)

    return new_tag


def push_branch_with_files(branch: str, code_files: list[dict], message: str) -> list[str]:
    """
    Tworzy/resetuje branch roboczy (od GITHUB_DEFAULT_BRANCH), zapisuje na
    nim podane pliki, commituje i pushuje - wyłącznie po to, żeby GitHub
    Actions (ci.yml, wyzwalany na push do "agent/**") mógł uruchomić testy
    PRZED ostateczną akceptacją przez QA (Review II).

    Po pushu wraca na branch domyślny, żeby nie zostawiać lokalnego repo w
    niespójnym stanie - docelowy commit na branchu domyślnym powstaje
    później, w versioning_node, dopiero po pełnej akceptacji.

    Zwraca listę zapisanych ścieżek względnych.
    """
    repo = _repo()
    repo.git.checkout(settings.GITHUB_DEFAULT_BRANCH)
    repo.git.checkout("-B", branch)

    written = fs_tools.write_code_files(code_files)

    repo.index.add(written)
    repo.index.commit(message)

    origin = repo.remote("origin")
    origin.push(branch, force=True)

    repo.git.checkout(settings.GITHUB_DEFAULT_BRANCH)
    return written
