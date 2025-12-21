#!/usr/bin/env python3
"""
Transcript Agent Client
Client for interacting with the Transcript Agent API
Supports streaming responses
"""

import requests
import time
import uuid
import json
from typing import Dict, Any, Optional, List, Iterator


class TranscriptClient:
    """Client for interacting with the Transcript Agent API"""

    def __init__(
            self,
            base_url: str = "http://localhost:8000"
    ):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.timeout = 300  # 5 minutes default timeout

    def _request(
            self,
            method: str,
            endpoint: str,
            data: Optional[Dict] = None,
            params: Optional[Dict] = None,
    ) -> Any:
        """Make API request"""
        url = f"{self.base_url}{endpoint}"

        try:
            response = self.session.request(
                method,
                url,
                json=data,
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise Exception(f"API request failed: {str(e)}")

    def health_check(self) -> Dict[str, Any]:
        """Check API health"""
        return self._request('GET', '/health')

    def load_transcript(
            self,
            conversation_id: str,
            transcript_path: str
    ) -> Dict[str, Any]:
        """Load transcript for a conversation"""
        params = {
            'conversation_id': conversation_id,
            'transcript_path': transcript_path
        }
        return self._request('POST', '/transcript/load', params=params)

    def create_task(
            self,
            conversation_id: str,
            user_id: str,
            prompt: str,
            transcript_path: Optional[str] = None,
            stream: bool = False
    ) -> str:
        """Create a new task and return task_id"""

        message_id = str(uuid.uuid4())

        data = {
            "conversation": {
                "uuid": conversation_id,
                "user_uuid": user_id
            },
            "message": {
                "uuid": message_id,
                "user_uuid": user_id,
                "conversation_uuid": conversation_id,
                "prompt": prompt
            },
            "stream": stream
        }

        # Add transcript_path if provided
        if transcript_path:
            data["transcript_path"] = transcript_path

        result = self._request('POST', '/transcript/task', data=data)
        return result['task_id']

    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get task status and result"""
        return self._request('GET', f'/transcript/task/{task_id}')

    def poll_task(
            self,
            task_id: str,
            poll_interval: int = 2,
            show_progress: bool = False
    ) -> Dict[str, Any]:
        """Poll task until completion with optional progress indicator"""
        start_time = time.time()
        dots = 0

        while time.time() - start_time < self.timeout:
            result = self.get_task_status(task_id)

            status = result.get('status')

            if status == 'FAILED':
                raise Exception(f"Task failed: {result.get('failure', 'Unknown error')}")

            if status != 'STARTED':
                return result

            if show_progress:
                dots = (dots + 1) % 4
                elapsed = int(time.time() - start_time)
                print(f"\rProcessing{'.' * dots}{' ' * (3 - dots)} ({elapsed}s)", end='', flush=True)

            time.sleep(poll_interval)

        raise TimeoutError(f"Task did not complete within {self.timeout} seconds")

    def ask_question(
            self,
            conversation_id: str,
            user_id: str,
            prompt: str,
            transcript_path: Optional[str] = None,
            show_progress: bool = True
    ) -> Dict[str, Any]:
        """Ask a question and wait for result"""

        # Create task
        task_id = self.create_task(
            conversation_id,
            user_id,
            prompt,
            transcript_path
        )

        # Poll for result
        result = self.poll_task(task_id, show_progress=show_progress)

        if show_progress:
            print("\r" + " " * 50 + "\r", end='', flush=True)

        return result

    def ask_question_streaming(
            self,
            conversation_id: str,
            user_id: str,
            prompt: str,
            transcript_path: Optional[str] = None
    ) -> Iterator[Dict[str, Any]]:
        """Ask a question and stream the response"""

        # Create task with streaming enabled
        task_id = self.create_task(
            conversation_id,
            user_id,
            prompt,
            transcript_path,
            stream=True
        )

        # Stream results
        url = f"{self.base_url}/transcript/task/{task_id}/stream"

        try:
            response = self.session.get(url, stream=True, timeout=self.timeout)
            response.raise_for_status()

            # Parse Server-Sent Events
            for line in response.iter_lines():
                if line:
                    line_str = line.decode('utf-8')

                    # SSE format: "data: {...}"
                    if line_str.startswith('data: '):
                        data_str = line_str[6:]  # Remove "data: " prefix
                        try:
                            event = json.loads(data_str)
                            yield event

                            # Stop if we receive end event
                            if event.get('type') == 'end':
                                break
                        except json.JSONDecodeError:
                            continue

        except requests.exceptions.RequestException as e:
            yield {
                "type": "error",
                "error": str(e)
            }

    def get_conversation_history(self, conversation_id: str) -> Dict[str, Any]:
        """Get conversation history (AgentSession)"""
        return self._request('GET', f'/transcript/history/{conversation_id}')

    def list_conversations(self, user_uuid: str, limit: int = 100) -> Dict[str, Any]:
        """List all conversations for a user"""
        return self._request('GET', '/conversations/list', params={'user_uuid': user_uuid, 'limit': limit})

    def get_conversation_messages(self, conversation_uuid: str, limit: int = 100) -> Dict[str, Any]:
        """Get all messages in a conversation with artifacts"""
        return self._request('GET', f'/conversations/{conversation_uuid}/messages', params={'limit': limit})


if __name__ == "__main__":
    # Simple test
    client = TranscriptClient()

    try:
        health = client.health_check()
        print(f"✓ Server is healthy")
        print(f"  Active conversations: {health['active_conversations']}")
        print(f"  Active tasks: {health['active_tasks']}")
    except Exception as e:
        print(f"✗ Server check failed: {e}")