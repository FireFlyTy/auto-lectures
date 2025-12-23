"""
Simple Conversation Repository using SQLite
With file_hash support for caching suggestions
"""

import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from dataclasses import dataclass, asdict
from enum import Enum
import threading


class ArtifactType(str, Enum):
    CHART = "chart"
    TABLE = "table"
    DATASET = "dataset"
    TEXT = "text"


@dataclass
class Conversation:
    uuid: str
    user_uuid: str
    title: Optional[str] = None
    file_hash: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message_count: int = 0


@dataclass
class Message:
    uuid: str
    conversation_uuid: str
    user_uuid: str
    task_id: Optional[str] = None
    prompt: Optional[str] = None
    answer: Optional[str] = None
    summary: Optional[str] = None
    created_at: Optional[str] = None
    artifacts: Optional[List[Dict]] = None


@dataclass
class Artifact:
    id: Optional[int] = None
    message_uuid: Optional[str] = None
    artifact_type: str = ArtifactType.TEXT
    name: Optional[str] = None
    path: Optional[str] = None
    data: Optional[str] = None
    created_at: Optional[str] = None


class ConversationRepository:

    def __init__(self, db_path: str = "./conversations_metadata.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_connection(self):
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=FULL")
            self._local.connection = conn
        return self._local.connection

    def _close_connection(self):
        if hasattr(self._local, 'connection') and self._local.connection is not None:
            try:
                self._local.connection.close()
            except:
                pass
            self._local.connection = None

    def _init_db(self):
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                uuid TEXT PRIMARY KEY,
                user_uuid TEXT NOT NULL,
                title TEXT,
                file_hash TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                message_count INTEGER DEFAULT 0
            )
        ''')

        # Миграция: добавляем file_hash если нет
        cursor.execute("PRAGMA table_info(conversations)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'file_hash' not in columns:
            cursor.execute('ALTER TABLE conversations ADD COLUMN file_hash TEXT')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                uuid TEXT PRIMARY KEY,
                conversation_uuid TEXT NOT NULL,
                user_uuid TEXT NOT NULL,
                task_id TEXT,
                prompt TEXT,
                answer TEXT,
                summary TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_uuid) REFERENCES conversations(uuid)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_uuid TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                name TEXT,
                path TEXT,
                data TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (message_uuid) REFERENCES messages(uuid)
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_uuid)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_conv_hash ON conversations(file_hash)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_uuid)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_msg_task ON messages(task_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_art_msg ON artifacts(message_uuid)')

        conn.commit()

    def create_or_update_conversation(
        self,
        conversation_uuid: str,
        user_uuid: str,
        title: Optional[str] = None,
        file_hash: Optional[str] = None
    ) -> Conversation:
        conn = self._get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()

        cursor.execute('SELECT * FROM conversations WHERE uuid = ?', (conversation_uuid,))
        existing = cursor.fetchone()

        if existing:
            cursor.execute('''
                UPDATE conversations 
                SET updated_at = ?, 
                    title = COALESCE(?, title), 
                    file_hash = COALESCE(?, file_hash)
                WHERE uuid = ?
            ''', (now, title, file_hash, conversation_uuid))
        else:
            cursor.execute('''
                INSERT INTO conversations (uuid, user_uuid, title, file_hash, created_at, updated_at, message_count)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            ''', (conversation_uuid, user_uuid, title, file_hash, now, now))

        conn.commit()
        return self.get_conversation(conversation_uuid)

    def get_conversation(self, conversation_uuid: str) -> Optional[Conversation]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM conversations WHERE uuid = ?', (conversation_uuid,))
        row = cursor.fetchone()

        if row:
            return Conversation(
                uuid=row['uuid'],
                user_uuid=row['user_uuid'],
                title=row['title'],
                file_hash=row['file_hash'] if 'file_hash' in row.keys() else None,
                created_at=row['created_at'],
                updated_at=row['updated_at'],
                message_count=row['message_count']
            )
        return None

    def list_conversations(self, user_uuid: str, limit: int = 100) -> List[Conversation]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM conversations 
            WHERE user_uuid = ? 
            ORDER BY updated_at DESC 
            LIMIT ?
        ''', (user_uuid, limit))

        conversations = []
        for row in cursor.fetchall():
            conversations.append(Conversation(
                uuid=row['uuid'],
                user_uuid=row['user_uuid'],
                title=row['title'],
                file_hash=row['file_hash'] if 'file_hash' in row.keys() else None,
                created_at=row['created_at'],
                updated_at=row['updated_at'],
                message_count=row['message_count']
            ))
        return conversations

    def delete_conversation(self, conversation_uuid: str):
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            DELETE FROM artifacts 
            WHERE message_uuid IN (SELECT uuid FROM messages WHERE conversation_uuid = ?)
        ''', (conversation_uuid,))
        cursor.execute('DELETE FROM messages WHERE conversation_uuid = ?', (conversation_uuid,))
        cursor.execute('DELETE FROM conversations WHERE uuid = ?', (conversation_uuid,))
        conn.commit()

    def hard_reset(self):
        self._close_connection()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("PRAGMA foreign_keys = OFF")
            cursor.execute("DROP TABLE IF EXISTS artifacts")
            cursor.execute("DROP TABLE IF EXISTS messages")
            cursor.execute("DROP TABLE IF EXISTS conversations")
            conn.commit()
            cursor.execute("VACUUM")
            conn.commit()
        finally:
            conn.close()

        self._init_db()
        print(f"Database hard reset completed: {self.db_path}")

    def create_message(
        self,
        message_uuid: str,
        conversation_uuid: str,
        user_uuid: str,
        task_id: Optional[str] = None,
        prompt: Optional[str] = None,
        answer: Optional[str] = None,
        summary: Optional[str] = None
    ) -> Message:
        conn = self._get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()

        cursor.execute('''
            INSERT INTO messages 
            (uuid, conversation_uuid, user_uuid, task_id, prompt, answer, summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (message_uuid, conversation_uuid, user_uuid, task_id, prompt, answer, summary, now))

        cursor.execute('''
            UPDATE conversations 
            SET message_count = message_count + 1, updated_at = ?
            WHERE uuid = ?
        ''', (now, conversation_uuid))

        conn.commit()
        return self.get_message(message_uuid)

    def update_message(
        self,
        message_uuid: str,
        answer: Optional[str] = None,
        summary: Optional[str] = None
    ) -> Optional[Message]:
        conn = self._get_connection()
        cursor = conn.cursor()

        updates = []
        params = []

        if answer is not None:
            updates.append('answer = ?')
            params.append(answer)
        if summary is not None:
            updates.append('summary = ?')
            params.append(summary)

        if updates:
            params.append(message_uuid)
            cursor.execute(f'UPDATE messages SET {", ".join(updates)} WHERE uuid = ?', params)
            conn.commit()

        return self.get_message(message_uuid)

    def get_message(self, message_uuid: str) -> Optional[Message]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM messages WHERE uuid = ?', (message_uuid,))
        row = cursor.fetchone()

        if row:
            cursor.execute('SELECT * FROM artifacts WHERE message_uuid = ?', (message_uuid,))
            artifacts = [dict(art_row) for art_row in cursor.fetchall()]

            return Message(
                uuid=row['uuid'],
                conversation_uuid=row['conversation_uuid'],
                user_uuid=row['user_uuid'],
                task_id=row['task_id'],
                prompt=row['prompt'],
                answer=row['answer'],
                summary=row['summary'],
                created_at=row['created_at'],
                artifacts=artifacts
            )
        return None

    def list_messages(self, conversation_uuid: str, limit: int = 100) -> List[Message]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT uuid FROM messages 
            WHERE conversation_uuid = ? 
            ORDER BY created_at ASC 
            LIMIT ?
        ''', (conversation_uuid, limit))

        messages = []
        for row in cursor.fetchall():
            msg = self.get_message(row['uuid'])
            if msg:
                messages.append(msg)
        return messages

    def create_artifact(
        self,
        message_uuid: str,
        artifact_type: str,
        name: Optional[str] = None,
        path: Optional[str] = None,
        data: Optional[Dict] = None
    ) -> Artifact:
        conn = self._get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        data_json = json.dumps(data) if data else None

        cursor.execute('''
            INSERT INTO artifacts (message_uuid, artifact_type, name, path, data, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (message_uuid, artifact_type, name, path, data_json, now))

        artifact_id = cursor.lastrowid
        conn.commit()

        return Artifact(
            id=artifact_id,
            message_uuid=message_uuid,
            artifact_type=artifact_type,
            name=name,
            path=path,
            data=data_json,
            created_at=now
        )

    def list_artifacts(self, message_uuid: str) -> List[Artifact]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM artifacts WHERE message_uuid = ?', (message_uuid,))

        artifacts = []
        for row in cursor.fetchall():
            artifacts.append(Artifact(
                id=row['id'],
                message_uuid=row['message_uuid'],
                artifact_type=row['artifact_type'],
                name=row['name'],
                path=row['path'],
                data=row['data'],
                created_at=row['created_at']
            ))
        return artifacts