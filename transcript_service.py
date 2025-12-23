from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional
from dataclasses import asdict
from datetime import datetime
import uvicorn
import asyncio
import uuid
from pathlib import Path
import json
import os
import shutil
import glob

from transcript_engine import calculate_file_hash, process_audio_with_deepgram, analyze_transcript_suggestions

from agent.agent_meta import create_agent
from agent.helper import AgentSession
from agents import Runner, SQLiteSession

try:
    from storage.conversation_repository import ConversationRepository, ArtifactType

    HAS_REPOSITORY = True
except ImportError:
    HAS_REPOSITORY = False
    ConversationRepository = None
    ArtifactType = None
    print("WARNING: conversation_repository not found.")


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


processing_status: Dict[str, Any] = {}

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = str(BASE_DIR / "conversations_metadata.db")
MEMORY_DB_PATH = str(BASE_DIR / "analyst_memory.db")
CONV_DIR = BASE_DIR / "conversations"
TRANSCRIPTS_DIR = BASE_DIR / "transcripts" / "processed"
SUGGESTIONS_DIR = BASE_DIR / "suggestions"


def update_processing_status(file_hash: str, stage: str, percent: int):
    processing_status[file_hash] = {
        "status": "processing",
        "stage": stage,
        "percent": percent
    }


def save_suggestions(file_hash: str, suggestions: list):
    SUGGESTIONS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = SUGGESTIONS_DIR / f"{file_hash}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(suggestions, f, ensure_ascii=False, indent=2)
    print(f"✓ Saved suggestions to {filepath}")


def load_suggestions(file_hash: str) -> list:
    filepath = SUGGESTIONS_DIR / f"{file_hash}.json"
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


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
                return f.read()
        except:
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
                    transcript_path = self.transcript_paths.get(
                        conversation_id,
                        f"transcripts/processed/{conversation_id}.txt"
                    )
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
            # Проверяем, есть ли уже сообщения - если нет, это первое сообщение
            existing_msgs = self.repo.list_messages(agent_session.conversation_id, limit=1)
            if not existing_msgs:
                # Первое сообщение - устанавливаем авто-имя
                auto_title = prompt[:50] + ("..." if len(prompt) > 50 else "")
                self.repo.create_or_update_conversation(agent_session.conversation_id, "default-user", title=auto_title)
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
            # Проверяем, есть ли уже сообщения - если нет, это первое сообщение
            existing_msgs = self.repo.list_messages(agent_session.conversation_id, limit=1)
            if not existing_msgs:
                # Первое сообщение - устанавливаем авто-имя
                auto_title = prompt[:50] + ("..." if len(prompt) > 50 else "")
                self.repo.create_or_update_conversation(agent_session.conversation_id, "default-user", title=auto_title)
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
        if hasattr(result, 'final_output'):
            return self._extract_answer_text_from_final(result.final_output)
        return str(result)

    def _extract_answer_text_from_final(self, final_output) -> str:
        if final_output is None:
            return ""
        if isinstance(final_output, str):
            return final_output
        if hasattr(final_output, 'text_content'):
            return final_output.text_content
        return str(final_output)

    def get_task_status(self, task_id: str) -> TaskStatus:
        if task_id not in self.tasks:
            raise HTTPException(status_code=404, detail="Task not found")
        return self.tasks[task_id]

    def reset(self):
        self.tasks = {}
        self.agents = {}
        self.transcript_paths = {}
        self.streaming_tasks = {}
        self.repo = None


def process_audio_background(file_bytes, transcript_path, file_hash, conversation_uuid, user_uuid, filename):
    try:
        def report_status(stage, pct):
            update_processing_status(file_hash, stage, pct)

        process_audio_with_deepgram(file_bytes, transcript_path, status_callback=report_status)

        with open(transcript_path, 'r', encoding='utf-8') as f:
            transcript_text = f.read()

        report_status("Generating suggestions...", 85)
        suggestions = analyze_transcript_suggestions(transcript_text, status_callback=report_status)

        if suggestions:
            save_suggestions(file_hash, suggestions)

        report_status("Finalizing...", 98)

        session = AgentSession(conversation_uuid, "")
        session.transcript = transcript_text
        session.save()

        if service.repo:
            # Сохраняем file_hash в базу!
            service.repo.create_or_update_conversation(
                conversation_uuid=conversation_uuid,
                user_uuid=user_uuid,
                title=filename,
                file_hash=file_hash
            )
            service.transcript_paths[conversation_uuid] = transcript_path

        processing_status[file_hash] = {"status": "completed", "percent": 100, "stage": "Ready"}
        print(f"✓ Processing complete: {conversation_uuid} (hash: {file_hash})")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Background processing error: {e}")
        processing_status[file_hash] = {"status": "error", "error": str(e)}


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
async def get_task_status_endpoint(task_id: str):
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
    if not service.repo:
        return {"conversations": []}
    convs = service.repo.list_conversations(user_uuid, limit)
    # Конвертируем dataclass в dict для JSON
    return {"conversations": [asdict(c) for c in convs]}


@app.get("/conversations/{conversation_uuid}/messages")
async def get_conversation_messages(conversation_uuid: str, limit: int = 100):
    if not service.repo:
        return {"messages": []}
    msgs = service.repo.list_messages(conversation_uuid, limit)
    return {"conversation_uuid": conversation_uuid, "messages": [asdict(m) for m in msgs]}


@app.get("/suggestions/{file_hash}")
async def get_suggestions(file_hash: str):
    return load_suggestions(file_hash)


@app.patch("/conversations/{conversation_uuid}/rename")
async def rename_conversation(conversation_uuid: str, title: str):
    """Переименовать conversation"""
    if service.repo:
        conn = service.repo._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE conversations SET title = ?, updated_at = ? WHERE uuid = ?',
            (title, datetime.now().isoformat(), conversation_uuid)
        )
        conn.commit()
        return {"status": "renamed", "title": title}
    raise HTTPException(status_code=400, detail="Repository not available")


# clear ДОЛЖЕН быть ДО {conversation_uuid}
@app.delete("/conversations/clear")
async def clear_history(user_uuid: str):
    global service, processing_status

    print("=" * 60)
    print("!!! STARTING COMPLETE NUCLEAR RESET !!!")
    print("=" * 60)

    if service.repo:
        try:
            service.repo.hard_reset()
            print("✓ Database hard reset completed")
        except Exception as e:
            print(f"✗ Database reset error: {e}")

    service.reset()
    processing_status.clear()

    for db_file in glob.glob(f"{MEMORY_DB_PATH}*"):
        if os.path.exists(db_file):
            try:
                os.remove(db_file)
                print(f"✓ Deleted: {db_file}")
            except Exception as e:
                print(f"✗ Error: {e}")

    folders = [CONV_DIR, TRANSCRIPTS_DIR, SUGGESTIONS_DIR, BASE_DIR / "transcripts"]
    for folder in folders:
        if folder.exists():
            try:
                shutil.rmtree(folder)
                print(f"✓ Deleted folder: {folder}")
            except Exception as e:
                print(f"✗ Error: {e}")

    for folder in [CONV_DIR, TRANSCRIPTS_DIR, SUGGESTIONS_DIR]:
        folder.mkdir(parents=True, exist_ok=True)
    print("✓ Recreated empty folders")

    if HAS_REPOSITORY and ConversationRepository is not None:
        service.repo = ConversationRepository(db_path=DB_PATH)
        print("✓ Repository reconnected")

    print("=" * 60)
    print("!!! RESET COMPLETE !!!")
    print("=" * 60)

    return {"status": "cleared_completely"}


@app.delete("/conversations/{conversation_uuid}")
async def delete_conversation(conversation_uuid: str):
    if service.repo:
        service.repo.delete_conversation(conversation_uuid)
    if conversation_uuid in service.agents:
        del service.agents[conversation_uuid]

    session_file = CONV_DIR / conversation_uuid
    if session_file.exists():
        try:
            session_file.unlink()
        except:
            pass

    return {"status": "deleted"}


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

    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    transcript_path = str(TRANSCRIPTS_DIR / f"{file_hash}.txt")

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

        existing_suggestions = load_suggestions(file_hash)
        if not existing_suggestions:
            suggestions = analyze_transcript_suggestions(session.transcript)
            if suggestions:
                save_suggestions(file_hash, suggestions)

        if service.repo:
            # Сохраняем file_hash в базу!
            service.repo.create_or_update_conversation(
                conversation_uuid=conversation_uuid,
                user_uuid=user_uuid,
                title=f"{file.filename} (Cached)",
                file_hash=file_hash
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
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    CONV_DIR.mkdir(exist_ok=True)
    SUGGESTIONS_DIR.mkdir(exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=8000)