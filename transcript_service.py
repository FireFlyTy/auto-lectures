from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional, AsyncGenerator
import uvicorn
import asyncio
import uuid
from pathlib import Path
import json
import os
import shutil  # Для удаления папок целиком
import glob

from transcript_engine import calculate_file_hash, process_audio_with_deepgram, analyze_transcript_suggestions

# Import the agent creator and helper
from agent.agent_meta import create_agent
from agent.helper import AgentSession

# OpenAI Agents imports
from agents import Runner, SQLiteSession

try:
    from storage.conversation_repository import ConversationRepository, ArtifactType

    HAS_REPOSITORY = True
except ImportError:
    HAS_REPOSITORY = False
    ConversationRepository = None
    ArtifactType = None
    print("WARNING: conversation_repository not found. Metadata storage disabled.")


# ============================================
# Data Models & Global State
# ============================================

class ConversationData(BaseModel):
    uuid: str
    user_uuid: str


class MessageData(BaseModel):
    uuid: str
    user_uuid: str
    conversation_uuid: str
    prompt: str


class TaskRequest(BaseModel):
    conversation: ConversationData
    message: MessageData
    transcript_path: Optional[str] = None
    stream: Optional[bool] = False


class TaskResponse(BaseModel):
    task_id: str


class TaskStatus(BaseModel):
    status: str
    result: Optional[Dict[str, Any]] = None
    failure: Optional[str] = None


# Global storage
processing_status = {}


def update_processing_status(file_hash: str, stage: str, percent: int):
    processing_status[file_hash] = {
        "status": "processing",
        "stage": stage,
        "percent": percent
    }

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = str(BASE_DIR / "conversations_metadata.db")
MEMORY_DB_PATH = str(BASE_DIR / "analyst_memory.db")
CONV_DIR = BASE_DIR / "conversations"
TRANSCRIPTS_DIR = BASE_DIR / "transcripts" / "processed"
class TranscriptService:
    def __init__(self):
        self.tasks: Dict[str, TaskStatus] = {}
        self.agents: Dict[str, Any] = {}
        self.transcript_paths: Dict[str, str] = {}
        self.streaming_tasks: Dict[str, asyncio.Queue] = {}

        if HAS_REPOSITORY and ConversationRepository is not None:
            print(f"DEBUG: Using Database at {DB_PATH}")
            self.repo = ConversationRepository(db_path=DB_PATH)
        else:
            self.repo = None

    def load_transcript(self, transcript_path: str) -> str:
        try:
            with open(transcript_path, 'r', encoding='utf-8') as f:
                transcript = f.read()
            return transcript
        except Exception as e:
            return ""

    async def create_task(self, request: TaskRequest) -> str:
        task_id = str(uuid.uuid4())
        self.tasks[task_id] = TaskStatus(status="STARTED")
        if request.stream:
            self.streaming_tasks[task_id] = asyncio.Queue()
        asyncio.create_task(self._process_task(task_id, request))
        return task_id

    async def _process_task(self, task_id: str, request: TaskRequest):
        stream_queue = self.streaming_tasks.get(task_id)
        try:
            conversation_id = request.conversation.uuid
            message_id = request.message.uuid
            prompt = request.message.prompt

            if request.transcript_path:
                self.transcript_paths[conversation_id] = request.transcript_path

            if conversation_id not in self.agents:
                agent_session = AgentSession(conversation_id, message_id)
                agent_session.load()

                if not agent_session.transcript:
                    transcript_path = self.transcript_paths.get(conversation_id,
                                                                f"transcripts/processed/{conversation_id}.txt")
                    if os.path.exists(transcript_path):
                        agent_session.transcript = self.load_transcript(transcript_path)
                        agent_session.save()

                transcript = agent_session.transcript or ""

                agent, sqlite_session = await create_agent(conversation_id=conversation_id, transcript=transcript)
                self.agents[conversation_id] = {
                    'agent': agent,
                    'sqlite_session': sqlite_session,
                    'agent_session': agent_session
                }
            else:
                agent_data = self.agents[conversation_id]
                agent = agent_data['agent']
                sqlite_session = agent_data['sqlite_session']
                agent_session = agent_data['agent_session']
                agent_session.message_id = message_id

            if request.stream and stream_queue:
                await self._run_agent_streaming(agent, prompt, sqlite_session, agent_session, task_id, stream_queue)
            else:
                await self._run_agent_non_streaming(agent, prompt, sqlite_session, agent_session, task_id)

        except Exception as e:
            import traceback
            traceback.print_exc()
            if stream_queue:
                await stream_queue.put({"type": "error", "error": str(e)})
                await stream_queue.put(None)
            self.tasks[task_id] = TaskStatus(status="FAILED", failure=str(e))
        finally:
            if task_id in self.streaming_tasks:
                del self.streaming_tasks[task_id]

    async def _run_agent_non_streaming(self, agent, prompt, sqlite_session, agent_session, task_id):
        if self.repo:
            self.repo.create_or_update_conversation(agent_session.conversation_id, "default-user", prompt[:50])
            self.repo.create_message(agent_session.message_id, agent_session.conversation_id, "default-user", task_id,
                                     prompt)

        result = await Runner.run(agent, prompt, session=sqlite_session, context=agent_session)
        answer_text = self._extract_answer_text(result)

        agent_session.add_answer(answer_text)
        agent_session.save()

        if self.repo:
            self.repo.update_message(agent_session.message_id, answer=answer_text)

        self.tasks[task_id] = TaskStatus(status="SUCCESS", result={'text': answer_text})

    async def _run_agent_streaming(self, agent, prompt, sqlite_session, agent_session, task_id, stream_queue):
        if self.repo:
            self.repo.create_or_update_conversation(agent_session.conversation_id, "default-user", prompt[:50])
            self.repo.create_message(agent_session.message_id, agent_session.conversation_id, "default-user", task_id,
                                     prompt)

        streamed = Runner.run_streamed(agent, prompt, session=sqlite_session, context=agent_session)
        full_text = ""

        async for event in streamed.stream_events():
            if event.type == "raw_response_event" and hasattr(event, 'data') and hasattr(event.data, 'delta'):
                delta = event.data.delta
                full_text += delta
                await stream_queue.put({"type": "delta", "delta": delta, "accumulated": full_text})

        final_output = streamed.final_output
        answer_text = self._extract_answer_text_from_final(final_output) or full_text

        agent_session.add_answer(answer_text)
        agent_session.save()

        if self.repo:
            self.repo.update_message(agent_session.message_id, answer=answer_text)

        await stream_queue.put({"type": "done", "text": answer_text})
        await stream_queue.put(None)
        self.tasks[task_id] = TaskStatus(status="SUCCESS", result={'text': answer_text})

    def _extract_answer_text(self, result) -> str:
        if hasattr(result, 'final_output'): return self._extract_answer_text_from_final(result.final_output)
        return str(result)

    def _extract_answer_text_from_final(self, final_output) -> str:
        if final_output is None: return ""
        if isinstance(final_output, str): return final_output
        if hasattr(final_output, 'text_content'): return final_output.text_content
        return str(final_output)

    def get_task_status(self, task_id: str) -> TaskStatus:
        if task_id not in self.tasks: raise HTTPException(status_code=404, detail="Task not found")
        return self.tasks[task_id]


# ============================================
# Background Process
# ============================================

def process_audio_background(file_bytes, transcript_path, file_hash, conversation_uuid, user_uuid, filename):
    try:
        def report_status(stage, pct):
            update_processing_status(file_hash, stage, pct)

        # 1. Transcribe
        process_audio_with_deepgram(file_bytes, transcript_path, status_callback=report_status)

        with open(transcript_path, 'r', encoding='utf-8') as f:
            transcript_text = f.read()

        # 2. Analyze Suggestions
        analyze_transcript_suggestions(transcript_text, status_callback=report_status)

        # 3. Finalize
        report_status("Finalizing conversation...", 98)

        session = AgentSession(conversation_uuid, "")
        session.transcript = transcript_text
        session.save()

        if service.repo:
            service.repo.create_or_update_conversation(
                conversation_uuid=conversation_uuid,
                user_uuid=user_uuid,
                title=f"{filename}"
            )
            service.transcript_paths[conversation_uuid] = transcript_path

        processing_status[file_hash] = {"status": "completed", "percent": 100, "stage": "Ready"}

    except Exception as e:
        print(f"Background processing error: {e}")
        processing_status[file_hash] = {"status": "error", "error": str(e)}


# ============================================
# FastAPI Application
# ============================================

app = FastAPI(title="Transcript Agent Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

service = TranscriptService()


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.post("/transcript/task", response_model=TaskResponse)
async def create_task(request: TaskRequest):
    task_id = await service.create_task(request)
    return TaskResponse(task_id=task_id)


@app.get("/transcript/task/{task_id}", response_model=TaskStatus)
async def get_task_status(task_id: str):
    return service.get_task_status(task_id)


@app.get("/transcript/task/{task_id}/stream")
async def stream_task_response(task_id: str):
    if task_id not in service.streaming_tasks:
        raise HTTPException(status_code=404, detail="Streaming not available")

    async def event_generator():
        queue = service.streaming_tasks[task_id]
        try:
            while True:
                event = await queue.get()
                if event is None:
                    yield f"data: {json.dumps({'type': 'end'})}\n\n"
                    break
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/conversations/list")
async def list_conversations(user_uuid: str, limit: int = 100):
    if not service.repo: return {"conversations": []}
    convs = service.repo.list_conversations(user_uuid, limit)
    return {"conversations": convs}


@app.get("/conversations/{conversation_uuid}/messages")
async def get_conversation_messages(conversation_uuid: str, limit: int = 100):
    if not service.repo: return {"messages": []}
    msgs = service.repo.list_messages(conversation_uuid, limit)
    return {"conversation_uuid": conversation_uuid, "messages": msgs}


@app.get("/conversations/{conversation_uuid}/suggestions")
async def get_suggestions(conversation_uuid: str):
    # Use logic from engine
    from transcript_engine import analyze_transcript_suggestions
    return analyze_transcript_suggestions("")


@app.delete("/conversations/{conversation_uuid}")
async def delete_conversation(conversation_uuid: str):
    if service.repo:
        service.repo.delete_conversation(conversation_uuid)
    if conversation_uuid in service.agents:
        del service.agents[conversation_uuid]
    return {"status": "deleted"}


# =========================================================
# ИСПРАВЛЕННАЯ ФУНКЦИЯ ПОЛНОГО УДАЛЕНИЯ (HARD RESET)
# =========================================================
@app.delete("/conversations/clear")
async def clear_history(user_uuid: str):
    print("!!! STARTING NUCLEAR RESET !!!")

    # 1. Сбрасываем память в RAM
    service.agents = {}

    # 2. Удаляем файлы баз данных (включая временные файлы .wal и .shm)
    # Используем glob, чтобы удалить "conversations_metadata.db", "...db-wal", "...db-shm"
    for db_file in glob.glob(f"{DB_PATH}*"):
        try:
            os.remove(db_file)
            print(f"Deleted DB file: {db_file}")
        except Exception as e:
            print(f"Error deleting {db_file}: {e}")

    # То же самое для памяти агента
    for db_file in glob.glob(f"{MEMORY_DB_PATH}*"):
        try:
            os.remove(db_file)
        except:
            pass

    # 3. Удаляем папки
    for folder in [CONV_DIR, TRANSCRIPTS_DIR]:
        if folder.exists():
            for item in folder.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                except Exception as e:
                    print(f"Error cleaning {item}: {e}")

    # 4. Пересоздаем пустую структуру БД прямо сейчас
    if service.repo:
        try:
            # Важно: переинициализируем соединение, так как файл был удален
            service.repo._init_db()
            print("Database structure re-created.")
        except Exception as e:
            print(f"Re-init error: {e}")

    return {"status": "cleared_completely"}


@app.get("/conversations/processing/{file_hash}")
async def get_processing_status_endpoint(file_hash: str):
    return processing_status.get(file_hash, {"status": "unknown"})


@app.post("/conversations/upload")
async def upload_audio_and_start(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        user_uuid: str = Form(...)
):
    file_bytes = await file.read()
    file_hash = calculate_file_hash(file_bytes)

    transcript_dir = "transcripts/processed"
    os.makedirs(transcript_dir, exist_ok=True)
    transcript_path = f"{transcript_dir}/{file_hash}.txt"

    conversation_uuid = str(uuid.uuid4())
    is_cached = os.path.exists(transcript_path)

    if not is_cached:
        processing_status[file_hash] = {"status": "uploading", "stage": "Queued", "percent": 0}
        background_tasks.add_task(
            process_audio_background,
            file_bytes,
            transcript_path,
            file_hash,
            conversation_uuid,
            user_uuid,
            file.filename
        )
    else:
        session = AgentSession(conversation_uuid, "")
        with open(transcript_path, 'r', encoding='utf-8') as f:
            session.transcript = f.read()
        session.save()

        if service.repo:
            service.repo.create_or_update_conversation(
                conversation_uuid=conversation_uuid,
                user_uuid=user_uuid,
                title=f"{file.filename} (Cached)"
            )
            service.transcript_paths[conversation_uuid] = transcript_path

    return {
        "status": "success",
        "conversation_uuid": conversation_uuid,
        "file_hash": file_hash,
        "is_cached": is_cached,
        "filename": file.filename
    }


if __name__ == "__main__":
    Path("transcripts/processed").mkdir(parents=True, exist_ok=True)
    Path("conversations").mkdir(exist_ok=True)
    uvicorn.run("transcript_service:app", host="0.0.0.0", port=8000, reload=True)