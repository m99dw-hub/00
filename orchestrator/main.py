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
_conversation_history: dict[str, list[dict]] = {}  # chat_id -> [{"role": "user"|"assistant", "content": str}]
_HISTORY_LIMIT = 40  # zabezpieczenie przed nieograniczonym wzrostem kosztu tokenow


def _append_history(chat_id: str, role: str, content: str) -> None:
    history = _conversation_history.setdefault(chat_id, [])
    history.append({"role": role, "content": content})
    del history[:-_HISTORY_LIMIT]


def _format_history(chat_id: str) -> str:
    history = _conversation_history.get(chat_id, [])
    return "\n".join(f"{h['role']}: {h['content']}" for h in history)


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
        _append_history(chat_id, "user", text)
        task_id = str(uuid.uuid4())[:8]

        await send_whatsapp_message(chat_id, f"🔧 [{task_id}] Przyjąłem zadanie, analizuję...")

        initial_state = {
            "task_id": task_id,
            "whatsapp_chat_id": chat_id,
            "raw_request": text,
            "conversation_history": _format_history(chat_id),
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
            reply = f"❓ [{task_id}] {final_state['clarification_question']}"
        elif final_state["status"] == "done":
            reply = f"✅ [{task_id}] Gotowe. {final_state.get('failure_reason', '')}"
        else:
            reply = f"❌ [{task_id}] {final_state.get('failure_reason', 'Niepowodzenie')}"

        _append_history(chat_id, "assistant", reply)
        await send_whatsapp_message(chat_id, reply)

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
