from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional, AsyncGenerator
import uvicorn
import asyncio
import uuid
from pathlib import Path
import json

# Import the agent creator and helper
from agent.agent_meta import create_agent
from agent.helper import AgentSession

# OpenAI Agents imports
from agents import Runner

try:
    from storage.conversation_repository import ConversationRepository, ArtifactType

    HAS_REPOSITORY = True
except ImportError:
    HAS_REPOSITORY = False
    ConversationRepository = None
    ArtifactType = None
    print("WARNING: conversation_repository not found. Metadata storage disabled.")
    print("To enable: Place conversation_repository.py in the same directory.")

# ============================================
# Data Models
# ============================================

class ConversationData(BaseModel):
    """Conversation identification"""
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
    """Task status response"""
    status: str  # STARTED, SUCCESS, FAILED
    result: Optional[Dict[str, Any]] = None
    failure: Optional[str] = None


# ============================================
# Service State
# ============================================

class TranscriptService:
    def __init__(self):
        self.tasks: Dict[str, TaskStatus] = {}
        self.agents: Dict[str, Any] = {}
        self.transcript_paths: Dict[str, str] = {}  # Store transcript paths per conversation
        self.streaming_tasks: Dict[str, asyncio.Queue] = {}  # Queues for streaming

        if HAS_REPOSITORY and ConversationRepository is not None:
            self.repo = ConversationRepository()
        else:
            self.repo = None
            print("INFO: Running without metadata repository (AgentSession only)")

    def load_transcript(self, transcript_path: str) -> str:
        try:
            with open(transcript_path, 'r', encoding='utf-8') as f:
                transcript = f.read()
            return transcript
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Transcript file not found: {transcript_path}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error loading transcript: {str(e)}")

    async def create_task(self, request: TaskRequest) -> str:
        task_id = str(uuid.uuid4())

        # Initialize task as started
        self.tasks[task_id] = TaskStatus(status="STARTED")

        # Create streaming queue if streaming is enabled
        if request.stream:
            self.streaming_tasks[task_id] = asyncio.Queue()

        # Process asynchronously
        asyncio.create_task(self._process_task(task_id, request))

        return task_id

    async def _process_task(self, task_id: str, request: TaskRequest):
        stream_queue = self.streaming_tasks.get(task_id)

        try:
            conversation_id = request.conversation.uuid
            message_id = request.message.uuid
            prompt = request.message.prompt

            # Store transcript path if provided in request
            if request.transcript_path:
                self.transcript_paths[conversation_id] = request.transcript_path

            # Get or create agent for this conversation
            if conversation_id not in self.agents:
                # Load or create AgentSession for storing answers
                agent_session = AgentSession(conversation_id, message_id)
                agent_session.load()

                # If session has no transcript, try to load from stored path or default
                if not agent_session.transcript:
                    if conversation_id in self.transcript_paths:
                        transcript_path = self.transcript_paths[conversation_id]
                    else:
                        # Try default location
                        transcript_path = f"transcripts/{conversation_id}.txt"

                    transcript = self.load_transcript(transcript_path)
                    # Save transcript to AgentSession
                    agent_session.transcript = transcript
                    agent_session.save()
                else:
                    transcript = agent_session.transcript

                # Create agent with SQLite session (returns agent, sqlite_session)
                agent, sqlite_session = await create_agent(
                    conversation_id=conversation_id,
                    transcript=transcript
                )

                self.agents[conversation_id] = {
                    'agent': agent,
                    'sqlite_session': sqlite_session,
                    'agent_session': agent_session
                }
            else:
                # Get existing sessions
                agent_data = self.agents[conversation_id]
                agent = agent_data['agent']
                sqlite_session = agent_data['sqlite_session']
                agent_session = agent_data['agent_session']
                agent_session.message_id = message_id

            # Run agent with streaming if enabled
            if request.stream and stream_queue:
                await self._run_agent_streaming(
                    agent, prompt, sqlite_session, agent_session,
                    task_id, stream_queue
                )
            else:
                await self._run_agent_non_streaming(
                    agent, prompt, sqlite_session, agent_session,
                    task_id
                )

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"Task {task_id} failed with error:\n{error_details}")

            # Send error to stream if streaming
            if stream_queue:
                await stream_queue.put({
                    "type": "error",
                    "error": str(e)
                })
                await stream_queue.put(None)  # End stream

            self.tasks[task_id] = TaskStatus(
                status="FAILED",
                failure=str(e)
            )
        finally:
            # Cleanup streaming queue
            if task_id in self.streaming_tasks:
                del self.streaming_tasks[task_id]

    async def _run_agent_non_streaming(
            self, agent, prompt, sqlite_session, agent_session, task_id
    ):
        """Run agent without streaming"""
        conversation_id = agent_session.conversation_id
        message_id = agent_session.message_id

        # Create conversation metadata if repository is available
        if self.repo:
            self.repo.create_or_update_conversation(
                conversation_uuid=conversation_id,
                user_uuid="default-user",  # Will be updated from request
                title=prompt[:50] if prompt else "Conversation"
            )

            # Create message record
            self.repo.create_message(
                message_uuid=message_id,
                conversation_uuid=conversation_id,
                user_uuid="default-user",
                task_id=task_id,
                prompt=prompt
            )

        # Run agent using Runner.run() with session and context
        result = await Runner.run(
            agent,
            prompt,
            session=sqlite_session,
            context=agent_session
        )

        # Extract answer text from result
        answer_text = self._extract_answer_text(result)

        # Add answer to AgentSession and save
        agent_session.add_answer(answer_text)
        agent_session.save()

        # Update message with answer if repository is available
        if self.repo:
            self.repo.update_message(
                message_uuid=message_id,
                answer=answer_text,
                summary=answer_text[:200] if answer_text else None
            )

        # Update task status
        self.tasks[task_id] = TaskStatus(
            status="SUCCESS",
            result={
                'text': answer_text,
                'conversation_id': conversation_id
            }
        )

    async def _run_agent_streaming(
            self, agent, prompt, sqlite_session, agent_session, task_id, stream_queue
    ):
        """Run agent with streaming output"""
        conversation_id = agent_session.conversation_id
        message_id = agent_session.message_id

        # Create conversation metadata if repository is available
        if self.repo:
            self.repo.create_or_update_conversation(
                conversation_uuid=conversation_id,
                user_uuid="default-user",
                title=prompt[:50] if prompt else "Conversation"
            )

            # Create message record
            self.repo.create_message(
                message_uuid=message_id,
                conversation_uuid=conversation_id,
                user_uuid="default-user",
                task_id=task_id,
                prompt=prompt
            )

        # Use Runner.run_streamed() for streaming
        streamed = Runner.run_streamed(
            agent,
            prompt,
            session=sqlite_session,
            context=agent_session
        )

        full_text = ""

        # Stream events
        async for event in streamed.stream_events():
            # Handle different event types
            if event.type == "raw_response_event":
                # Text delta event
                if hasattr(event, 'data') and hasattr(event.data, 'delta'):
                    delta = event.data.delta
                    full_text += delta

                    # Send delta to stream
                    await stream_queue.put({
                        "type": "delta",
                        "delta": delta,
                        "accumulated": full_text
                    })

            elif event.type == "run_item_stream_event":
                # Tool call or other events
                if hasattr(event.item, 'type'):
                    await stream_queue.put({
                        "type": "event",
                        "event_type": event.item.type,
                        "data": str(event.item)
                    })

        # Get final output
        final_output = streamed.final_output
        answer_text = self._extract_answer_text_from_final(final_output) or full_text

        # Add answer to AgentSession and save
        agent_session.add_answer(answer_text)
        agent_session.save()

        # Update message with answer if repository is available
        if self.repo:
            self.repo.update_message(
                message_uuid=message_id,
                answer=answer_text,
                summary=answer_text[:200] if answer_text else None
            )

        # Send final message
        await stream_queue.put({
            "type": "done",
            "text": answer_text,
            "conversation_id": conversation_id
        })

        # End stream
        await stream_queue.put(None)

        # Update task status
        self.tasks[task_id] = TaskStatus(
            status="SUCCESS",
            result={
                'text': answer_text,
                'conversation_id': conversation_id
            }
        )

        # Use Runner.run_streamed() for streaming
        streamed = Runner.run_streamed(
            agent,
            prompt,
            session=sqlite_session,
            context=agent_session
        )

        full_text = ""

        # Stream events
        async for event in streamed.stream_events():
            # Handle different event types
            if event.type == "raw_response_event":
                # Text delta event
                if hasattr(event, 'data') and hasattr(event.data, 'delta'):
                    delta = event.data.delta
                    full_text += delta

                    # Send delta to stream
                    await stream_queue.put({
                        "type": "delta",
                        "delta": delta,
                        "accumulated": full_text
                    })

            elif event.type == "run_item_stream_event":
                # Tool call or other events
                if hasattr(event.item, 'type'):
                    await stream_queue.put({
                        "type": "event",
                        "event_type": event.item.type,
                        "data": str(event.item)
                    })

        # Get final output
        final_output = streamed.final_output
        answer_text = self._extract_answer_text_from_final(final_output) or full_text

        # Add answer to AgentSession and save
        agent_session.add_answer(answer_text)
        agent_session.save()

        # Update message with answer
        self.repo.update_message(
            message_uuid=message_id,
            answer=answer_text,
            summary=answer_text[:200] if answer_text else None
        )

        # Send final message
        await stream_queue.put({
            "type": "done",
            "text": answer_text,
            "conversation_id": conversation_id
        })

        # End stream
        await stream_queue.put(None)

        # Update task status
        self.tasks[task_id] = TaskStatus(
            status="SUCCESS",
            result={
                'text': answer_text,
                'conversation_id': conversation_id
            }
        )

    def _extract_answer_text(self, result) -> str:
        """Extract answer text from Runner result"""
        answer_text = ""

        # Try to get text from final_output if available
        if hasattr(result, 'final_output'):
            final_output = result.final_output
            if isinstance(final_output, str):
                answer_text = final_output
            elif hasattr(final_output, 'text_content'):
                answer_text = final_output.text_content
            elif hasattr(final_output, '__dict__'):
                answer_text = str(final_output)
            else:
                answer_text = str(final_output)

        # If no final_output, try to get from new_items or messages
        elif hasattr(result, 'new_items') and result.new_items:
            from agents import ItemHelpers
            answer_text = ItemHelpers.text_message_outputs(result.new_items)

        elif hasattr(result, 'messages') and result.messages:
            # Get last message content
            last_msg = result.messages[-1]
            if hasattr(last_msg, 'content'):
                if isinstance(last_msg.content, list):
                    for item in last_msg.content:
                        if hasattr(item, 'text'):
                            answer_text += item.text
                else:
                    answer_text = str(last_msg.content)
        else:
            answer_text = str(result)

        return answer_text

    def _extract_answer_text_from_final(self, final_output) -> str:
        """Extract answer text from final output object"""
        if final_output is None:
            return ""

        if isinstance(final_output, str):
            return final_output

        if hasattr(final_output, 'text_content'):
            return final_output.text_content

        if hasattr(final_output, '__dict__'):
            return str(final_output)

        return str(final_output)

    def get_task_status(self, task_id: str) -> TaskStatus:
        """Get task status"""
        if task_id not in self.tasks:
            raise HTTPException(status_code=404, detail="Task not found")
        return self.tasks[task_id]

    def get_session(self, conversation_id: str) -> Optional[AgentSession]:
        """Get AgentSession for a conversation"""
        if conversation_id in self.agents:
            return self.agents[conversation_id]['agent_session']

        # Try to load from disk
        session = AgentSession(conversation_id, "")
        session.load()
        if session.transcript:
            return session
        return None


# ============================================
# FastAPI Application
# ============================================

app = FastAPI(title="Transcript Agent Service")
service = TranscriptService()


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "active_conversations": len(service.agents),
        "active_tasks": len([t for t in service.tasks.values() if t.status == "STARTED"])
    }


@app.post("/transcript/task", response_model=TaskResponse)
async def create_task(request: TaskRequest):
    """Create a new task"""
    task_id = await service.create_task(request)
    return TaskResponse(task_id=task_id)


@app.get("/transcript/task/{task_id}", response_model=TaskStatus)
async def get_task_status(task_id: str):
    """Get task status"""
    return service.get_task_status(task_id)


@app.get("/transcript/task/{task_id}/stream")
async def stream_task_response(task_id: str):
    """Stream task response in real-time (Server-Sent Events)"""

    if task_id not in service.streaming_tasks:
        raise HTTPException(status_code=404, detail="Streaming not available for this task")

    async def event_generator() -> AsyncGenerator[str, None]:
        """Generate Server-Sent Events"""
        queue = service.streaming_tasks[task_id]

        try:
            while True:
                # Get next event from queue
                event = await queue.get()

                # None signals end of stream
                if event is None:
                    yield f"data: {json.dumps({'type': 'end'})}\n\n"
                    break

                # Send event as SSE
                yield f"data: {json.dumps(event)}\n\n"

        except Exception as e:
            error_event = {
                "type": "error",
                "error": str(e)
            }
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.post("/transcript/load")
async def load_transcript(conversation_id: str, transcript_path: str):
    """Load transcript for a conversation"""
    try:
        transcript = service.load_transcript(transcript_path)

        # Store the transcript path for this conversation
        service.transcript_paths[conversation_id] = transcript_path

        # Create session and save transcript
        session = AgentSession(conversation_id, "")
        session.transcript = transcript
        session.save()

        return {
            "status": "success",
            "conversation_id": conversation_id,
            "transcript_length": len(transcript)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/transcript/history/{conversation_id}")
async def get_conversation_history(conversation_id: str):
    """Get conversation history"""
    session = service.get_session(conversation_id)

    if not session:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "conversation_id": conversation_id,
        "transcript_length": len(session.transcript),
        "answers": session.answers
    }


@app.get("/conversations/list")
async def list_conversations(user_uuid: str, limit: int = 100):
    """List all conversations for a user"""
    if not service.repo:
        raise HTTPException(
            status_code=503,
            detail="Metadata repository not available. Add conversation_repository.py to enable."
        )

    conversations = service.repo.list_conversations(user_uuid, limit)

    return {
        "conversations": [
            {
                "uuid": conv.uuid,
                "user_uuid": conv.user_uuid,
                "title": conv.title,
                "created_at": conv.created_at,
                "updated_at": conv.updated_at,
                "message_count": conv.message_count
            }
            for conv in conversations
        ]
    }


@app.get("/conversations/{conversation_uuid}/messages")
async def get_conversation_messages(conversation_uuid: str, limit: int = 100):
    """Get all messages in a conversation"""
    if not service.repo:
        raise HTTPException(
            status_code=503,
            detail="Metadata repository not available. Add conversation_repository.py to enable."
        )

    messages = service.repo.list_messages(conversation_uuid, limit)

    return {
        "conversation_uuid": conversation_uuid,
        "messages": [
            {
                "uuid": msg.uuid,
                "prompt": msg.prompt,
                "answer": msg.answer,
                "summary": msg.summary,
                "created_at": msg.created_at,
                "artifacts": msg.artifacts
            }
            for msg in messages
        ]
    }


# ============================================
# Main Entry Point
# ============================================

def main():
    """Run the service"""
    import argparse

    parser = argparse.ArgumentParser(description='Transcript Agent Service')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=8000, help='Port to bind to')
    parser.add_argument('--reload', action='store_true', help='Enable auto-reload')

    args = parser.parse_args()

    # Ensure required directories exist
    Path("transcripts").mkdir(exist_ok=True)
    Path("conversations").mkdir(exist_ok=True)

    uvicorn.run(
        "transcript_service:app",
        host=args.host,
        port=args.port,
        reload=args.reload
    )


if __name__ == "__main__":
    main()