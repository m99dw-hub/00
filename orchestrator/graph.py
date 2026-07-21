"""
Graf LangGraph - serce orchestratora.

Wezly odpowiadaja diagramowi architektury:
clarify -> load_requirements -> decompose -> assign_agent -> review1
  -> (NOK) assign_agent
  -> (OK) review2 -> (NOK) assign_agent
                   -> (OK) [kolejne podzadanie? -> assign_agent : wersjonowanie]
retry_count wspolny dla review1 + review2, max settings.MAX_RETRIES -> failure_report
"""
import json
import os
import uuid

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from config import settings
from state import TaskState
from agents.base import call_agent, call_agent_json, AgentJSONError
from prompts.system_prompts import (
    ORCHESTRATOR, REQUIREMENTS, UX, ARCHITECT, DEVELOPER, REVIEWER, QA, DEVOPS,
)
from tools import git_tools, github_actions, fs_tools


# ---- wezly ----------------------------------------------------------------

async def clarify_node(state: TaskState) -> TaskState:
    # Odpowiedz agenta orchestratora to CELOWO albo JSON (lista podzadan),
    # albo zwykly tekst pytania - dlatego nie mozemy tu bezwarunkowo wymusic
    # JSON-a (call_agent_json), tylko probujemy sparsowac i lagodnie
    # przechodzimy na tryb "pytanie doprecyzowujace", jesli to nie JSON.
    from agents.base import strip_code_fence

    response = await call_agent("orchestrator", ORCHESTRATOR, state["raw_request"])
    try:
        parsed = json.loads(strip_code_fence(response))
        if not isinstance(parsed, list):
            raise json.JSONDecodeError("oczekiwano listy", response, 0)
    except json.JSONDecodeError:
        state["needs_clarification"] = True
        state["clarification_question"] = response
        return state

    state["needs_clarification"] = False
    state["subtasks"] = [
        {"id": str(uuid.uuid4())[:8], "description": s["description"],
         "agent": s["agent"], "status": "pending"}
        for s in parsed
    ]
    return state


async def load_requirements_node(state: TaskState) -> TaskState:
    with open(settings.REQUIREMENTS_FILE, encoding="utf-8") as f:
        current = f.read()

    updated = await call_agent(
        "requirements", REQUIREMENTS,
        f"Aktualne REQUIREMENTS.md:\n{current}\n\nNowe zadanie:\n{state['raw_request']}",
    )

    if updated.strip() != current.strip():
        with open(settings.REQUIREMENTS_FILE, "w", encoding="utf-8") as f:
            f.write(updated)
        state["requirements_updated"] = True

    state["requirements_snapshot"] = updated
    return state


async def assign_agent_node(state: TaskState) -> TaskState:
    subtask = state["subtasks"][state["current_subtask_idx"]]
    role = subtask["agent"]
    prompt_map = {"ux": UX, "architect": ARCHITECT, "developer": DEVELOPER, "devops": DEVOPS}

    # Kazda nowa proba (pierwsza albo po poprawkach) unieważnia poprzedni
    # wynik CI - zostanie ewentualnie odswiezony w push_for_ci_node.
    state["ci_result"] = None

    context = f"Zadanie: {subtask['description']}\n\nWymagania:\n{state['requirements_snapshot']}"
    if state.get("review1_feedback"):
        context += f"\n\nFeedback z Review I (popraw dokladnie to):\n{state['review1_feedback']}"
    if state.get("review2_feedback"):
        context += f"\n\nFeedback z Review II (popraw dokladnie to):\n{state['review2_feedback']}"

    if role == "developer":
        # Developer zwraca liste plikow (JSON) - patrz prompts.DEVELOPER.
        # Jesli model uporczywie nie zwroci poprawnego JSON-a nawet po
        # probach naprawy w call_agent_json, nie ubijamy calego zadania:
        # traktujemy to jako artefakt do odrzucenia przez Review I (naturalny
        # feedback + zwiekszenie retry_count przez istniejaca petle),
        # zamiast osobnej sciezki bledu.
        try:
            files = await call_agent_json("developer", DEVELOPER, context)
            state["code_files"] = files
            state["artifact"] = "\n\n".join(
                f"### {f['path']}\n```kotlin\n{f['content']}\n```" for f in files
            )
        except AgentJSONError as exc:
            state["code_files"] = []
            state["artifact"] = (
                f"[BLAD] Developer nie zwrocil poprawnej listy plikow JSON: {exc}"
            )
    else:
        artifact = await call_agent(role, prompt_map.get(role, DEVELOPER), context)
        state["artifact"] = artifact
        state["code_files"] = None

    subtask["status"] = "in_progress"
    return state


async def review1_node(state: TaskState) -> TaskState:
    try:
        parsed = await call_agent_json(
            "reviewer", REVIEWER, f"Kod do review:\n{state['artifact']}",
        )
    except AgentJSONError as exc:
        # Niepoprawna odpowiedz recenzenta traktujemy jak NOK - agent
        # wykonawczy dostanie o tym info i graf naturalnie zwiekszy retry_count,
        # zamiast wywalac cale zadanie z powodu usterki formatu po stronie LLM.
        state["review1_feedback"] = f"Błąd techniczny recenzenta (spróbuj ponownie): {exc}"
        state["retry_count"] += 1
        return state

    if parsed.get("passed"):
        state["review1_feedback"] = None
    else:
        state["review1_feedback"] = parsed.get(
            "feedback", "Recenzent odrzucił kod bez podania konkretnego powodu."
        )
        state["retry_count"] += 1
    return state


async def push_for_ci_node(state: TaskState) -> TaskState:
    """
    Pcha kod developera na branch roboczy (agent/{task_id}-{subtask_id}),
    co automatycznie wyzwala GitHub Actions (ci.yml reaguje na push do
    "agent/**"), i czeka na wynik - PRZED oceną QA (Review II), żeby QA
    mogło uwzględnić realne testy instrumentalne, a nie tylko ocenę LLM.

    Błąd pushu/CI (np. chwilowy brak sieci) nie blokuje całego zadania -
    QA po prostu oceni kod bez wsparcia CI, z odnotowaniem tego faktu.
    """
    subtask = state["subtasks"][state["current_subtask_idx"]]
    branch = f"agent/{state['task_id']}-{subtask['id']}"

    try:
        git_tools.push_branch_with_files(
            branch=branch,
            code_files=state["code_files"],
            message=f"WIP CI check [{state['task_id']}]: {subtask['description'][:72]}",
        )
        state["ci_result"] = await github_actions.wait_for_ci_result(branch)
    except Exception as exc:  # noqa: BLE001 - CI/push nie moze zablokowac calego zadania
        state["ci_result"] = {"conclusion": "unavailable", "url": None, "error": str(exc)}

    return state


async def review2_node(state: TaskState) -> TaskState:
    ci_result = state.get("ci_result")
    ci_note = ""
    if ci_result:
        if ci_result.get("conclusion") == "unavailable":
            ci_note = (
                f"\n\n(Wynik CI niedostępny - {ci_result.get('error', 'nieznany błąd')}. "
                "Oceń na podstawie samego kodu.)"
            )
        else:
            ci_note = f"\n\nWynik testów CI (GitHub Actions): {ci_result['conclusion']}"
            if ci_result.get("url"):
                ci_note += f" ({ci_result['url']})"

    try:
        parsed = await call_agent_json(
            "qa", QA,
            f"Kod:\n{state['artifact']}\n\nWymagania:\n{state['requirements_snapshot']}{ci_note}",
        )
    except AgentJSONError as exc:
        state["review2_feedback"] = f"Błąd techniczny QA (spróbuj ponownie): {exc}"
        state["retry_count"] += 1
        return state

    subtask = state["subtasks"][state["current_subtask_idx"]]

    if parsed.get("passed"):
        state["review2_feedback"] = None
        subtask["status"] = "done"
        # Zaakceptowany kod zapisujemy na dysk NATYCHMIAST (nie czekamy do
        # konca calego zadania) - jesli kolejne podzadanie zawiedzie po
        # 3 probach, praca nad juz zaakceptowanymi plikami sie nie marnuje.
        if state.get("code_files"):
            written = fs_tools.write_code_files(state["code_files"])
            state["written_files"] = sorted(set(state.get("written_files", []) + written))
    else:
        state["review2_feedback"] = parsed.get(
            "feedback", "QA odrzuciło kod bez podania konkretnego powodu."
        )
        state["retry_count"] += 1
    return state


async def versioning_node(state: TaskState) -> TaskState:
    try:
        parsed = await call_agent_json(
            "devops", DEVOPS,
            f"Zadanie: {state['raw_request']}\nRealizacja: {state['artifact'][:2000]}",
        )
        version_bump = parsed.get("version_bump", "patch")
        changelog_entry = parsed.get("changelog_entry", state["raw_request"][:120])
    except AgentJSONError as exc:
        # Cala reszta pracy (zaakceptowany, zapisany kod) jest juz na dysku -
        # blad formatu w samym DevOps agencie nie powinien zniweczyc tego
        # dorobku. Stosujemy bezpieczny domyslny bump i jawnie to odnotowujemy.
        version_bump = "patch"
        changelog_entry = (
            f"{state['raw_request'][:120]} "
            f"(uwaga: agent DevOps zwrócił niepoprawny JSON, zastosowano "
            f"domyślny bump 'patch' - {exc})"
        )

    state["version_bump"] = version_bump

    with open(settings.CHANGELOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n- {changelog_entry}")

    written_files = state.get("written_files", [])
    files_to_commit = [settings.CHANGELOG_FILE] + [
        os.path.join(settings.ANDROID_REPO_PATH, rel) for rel in written_files
    ]

    new_tag = git_tools.commit_and_tag(
        files=files_to_commit,
        message=f"[{state['task_id']}] {state['raw_request'][:72]}",
        version_bump=version_bump,
        branch=settings.GITHUB_DEFAULT_BRANCH,
    )
    state["status"] = "done"
    state["failure_reason"] = (
        f"Wydano {new_tag} ({len(written_files)} plik(ów) kodu zmienionych)"
    )
    return state


async def failure_report_node(state: TaskState) -> TaskState:
    state["status"] = "failed"
    reason = state.get("review2_feedback") or state.get("review1_feedback") or "nieznany blad"
    state["failure_reason"] = (
        f"Zadanie nie przeszlo weryfikacji po {settings.MAX_RETRIES} probach.\n"
        f"Ostatni powod: {reason}"
    )
    return state


# ---- warunki routingu -------------------------------------------------------

def after_clarify(state: TaskState) -> str:
    return "end_clarify" if state["needs_clarification"] else "load_requirements"


def after_review1(state: TaskState) -> str:
    if state["review1_feedback"] is None:
        subtask = state["subtasks"][state["current_subtask_idx"]]
        if subtask["agent"] == "developer" and state.get("code_files"):
            return "push_for_ci"
        return "review2"
    return "failure_report" if state["retry_count"] >= settings.MAX_RETRIES else "assign_agent"


def after_review2(state: TaskState) -> str:
    if state["review2_feedback"] is not None:
        return "failure_report" if state["retry_count"] >= settings.MAX_RETRIES else "assign_agent"

    state["current_subtask_idx"] += 1
    if state["current_subtask_idx"] < len(state["subtasks"]):
        return "assign_agent"
    return "versioning"


# ---- budowa grafu ------------------------------------------------------------

def build_graph():
    g = StateGraph(TaskState)

    g.add_node("clarify", clarify_node)
    g.add_node("load_requirements", load_requirements_node)
    g.add_node("assign_agent", assign_agent_node)
    g.add_node("review1", review1_node)
    g.add_node("push_for_ci", push_for_ci_node)
    g.add_node("review2", review2_node)
    g.add_node("versioning", versioning_node)
    g.add_node("failure_report", failure_report_node)

    g.set_entry_point("clarify")
    g.add_conditional_edges("clarify", after_clarify, {
        "end_clarify": END,
        "load_requirements": "load_requirements",
    })
    g.add_edge("load_requirements", "assign_agent")
    g.add_edge("assign_agent", "review1")
    g.add_conditional_edges("review1", after_review1, {
        "push_for_ci": "push_for_ci",
        "review2": "review2",
        "assign_agent": "assign_agent",
        "failure_report": "failure_report",
    })
    g.add_edge("push_for_ci", "review2")
    g.add_conditional_edges("review2", after_review2, {
        "assign_agent": "assign_agent",
        "versioning": "versioning",
        "failure_report": "failure_report",
    })
    g.add_edge("versioning", END)
    g.add_edge("failure_report", END)

    return g


async def get_compiled_graph():
    graph = build_graph()
    os.makedirs("checkpoints", exist_ok=True)    
    saver = AsyncSqliteSaver.from_conn_string("checkpoints/state.sqlite")
    return graph.compile(checkpointer=await saver.__aenter__())
