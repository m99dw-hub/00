"""
Definicja stanu przeplywajacego przez graf LangGraph.
Jeden TaskState = jedno zadanie zlecone na WhatsApp (moze rozpadac sie
na wiele podzadan, ktore graf przetwarza kolejno).
"""
from typing import TypedDict, Literal, Optional


class Subtask(TypedDict):
    id: str
    description: str
    agent: str  # "ux" | "architect" | "developer" | "devops" ...
    status: Literal["pending", "in_progress", "done", "failed"]


class TaskState(TypedDict):
    task_id: str
    whatsapp_chat_id: str
    raw_request: str  # oryginalna wiadomosc od uzytkownika
    conversation_history: str  # sformatowana historia dotychczasowej rozmowy z tym czatem

    needs_clarification: bool
    clarification_question: Optional[str]

    requirements_snapshot: str  # tresc REQUIREMENTS.md w momencie startu
    requirements_updated: bool

    subtasks: list[Subtask]
    current_subtask_idx: int

    artifact: Optional[str]  # tekstowa reprezentacja pracy agenta (do review LLM)
    code_files: Optional[list[dict]]  # [{"path":..., "content":...}] - tylko dla developera
    written_files: list[str]  # sciezki wzgledne juz zapisane na dysku (akumulowane w toku zadania)
    ci_result: Optional[dict]  # {"conclusion": "success|failure|...", "url": ..., "error": ...}
    review1_feedback: Optional[str]
    review2_feedback: Optional[str]
    retry_count: int  # wspolny licznik dla review1 + review2, max settings.MAX_RETRIES

    status: Literal["clarifying", "in_progress", "done", "failed"]
    failure_reason: Optional[str]

    version_bump: Optional[Literal["patch", "minor", "major"]]
