"""
FastAPI - punkt wejscia orchestratora.

Odbiera zadania z mostu WhatsApp (POST /task), uruchamia graf LangGraph,
wysyla statusy posrednie i wynik koncowy z powrotem do mostu (ktory
przekazuje je na WhatsApp).

Uruchamianie: uvicorn main:app --host 127.0.0.1 --port 8000
"""
import asyncio
import uuid
import traceback
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from config import settings
from graph import get_compiled_graph
from tools import cost_monitor

app = FastAPI(title="android-agent-orchestrator")

_graph = None
_task_queue: asyncio.Queue = asyncio.Queue()  # sekwencyjne przetwarzanie zadan
_pending_clarifications: dict[str, str] = {}  # chat_id -> tresc pierwotnego zadania czekajacego na doprecyzowanie

class IncomingTask(BaseModel):
    chat_id: str
    text: str


async def send_whatsapp_message(chat_id: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            await client.post(settings.BRIDGE_SEND_URL, json={"chat_id": chat_id, "text": text})
        except httpx.HTTPError:
            pass  # most moze byc chwilowo niedostepny - nie wywalaj calego zadania


async def _worker():
    """Przetwarza zadania z kolejki jedno po drugim (bezpieczne dla malego VPS)."""
    global _graph
    while True:
        chat_id, text = await _task_queue.get()
        if chat_id in _pending_clarifications:
            original = _pending_clarifications.pop(chat_id)
            text = f"{original}\n\nDoprecyzowanie od użytkownika: {text}"

        task_id = str(uuid.uuid4())[:8]

        await send_whatsapp_message(chat_id, f"🔧 [{task_id}] Przyjąłem zadanie, analizuję...")

        initial_state = {
            "task_id": task_id,
            "whatsapp_chat_id": chat_id,
            "raw_request": text,
            "needs_clarification": False,
            "clarification_question": None,
            "requirements_snapshot": "",
            "requirements_updated": False,
            "subtasks": [],
            "current_subtask_idx": 0,
            "artifact": None,
            "code_files": None,
            "written_files": [],
            "ci_result": None,
            "review1_feedback": None,
            "review2_feedback": None,
            "retry_count": 0,
            "status": "in_progress",
            "failure_reason": None,
            "version_bump": None,
        }

        config = {"configurable": {"thread_id": task_id}}
        try:
            final_state = await _graph.ainvoke(initial_state, config=config)
        except Exception as exc:  # noqa: BLE001 - chcemy zlapac wszystko i zaraportowac
            traceback.print_exc()
            await send_whatsapp_message(chat_id, f"🛑 [{task_id}] Błąd systemowy: {exc}")
            _task_queue.task_done()
            continue

        if final_state.get("needs_clarification"):
            await send_whatsapp_message(
                chat_id, f"❓ [{task_id}] {final_state['clarification_question']}"
            )
        elif final_state["status"] == "done":
            await send_whatsapp_message(
                chat_id, f"✅ [{task_id}] Gotowe. {final_state.get('failure_reason', '')}"
            )
        else:
            await send_whatsapp_message(
                chat_id, f"❌ [{task_id}] {final_state.get('failure_reason', 'Niepowodzenie')}"
            )

        _task_queue.task_done()


async def _cost_watcher():
    while True:
        await cost_monitor.check_and_alert(send_whatsapp_message)
        await asyncio.sleep(3600)  # co godzine


@app.on_event("startup")
async def startup():
    global _graph
    _graph = await get_compiled_graph()
    asyncio.create_task(_worker())
    asyncio.create_task(_cost_watcher())


@app.post("/task")
async def receive_task(task: IncomingTask):
    if task.chat_id != settings.ALLOWED_WHATSAPP_JID:
        raise HTTPException(status_code=403, detail="Nieautoryzowany nadawca")

    await _task_queue.put((task.chat_id, task.text))
    return {"status": "queued", "queue_size": _task_queue.qsize()}


@app.get("/health")
async def health():
    return {"status": "ok", "queue_size": _task_queue.qsize()}
