"""
Simple Conversation Repository using SQLite
Stores conversation metadata, messages, and artifacts
"""

import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum


class ArtifactType(str, Enum):
    """Types of artifacts"""
    CHART = "chart"
    TABLE = "table"
    DATASET = "dataset"
    TEXT = "text"


@dataclass
class Conversation:
    """Conversation model"""
    uuid: str
    user_uuid: str
    title: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message_count: int = 0


@dataclass
class Message:
    """Message model"""
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
    """Artifact model"""
    id: Optional[int] = None
    message_uuid: Optional[str] = None
    artifact_type: str = ArtifactType.TEXT
    name: Optional[str] = None
    path: Optional[str] = None
    data: Optional[str] = None  # JSON data if needed
    created_at: Optional[str] = None


class ConversationRepository:
    """Repository for managing conversations, messages, and artifacts in SQLite"""
    
    def __init__(self, db_path: str = "./conversations_metadata.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Conversations table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS conversations (
                    uuid TEXT PRIMARY KEY,
                    user_uuid TEXT NOT NULL,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    message_count INTEGER DEFAULT 0
                )
            ''')
            
            # Messages table
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
            
            # Artifacts table
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
            
            # Indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_uuid)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_uuid)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_msg_task ON messages(task_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_art_msg ON artifacts(message_uuid)')
            
            conn.commit()
    
    @contextmanager
    def _get_connection(self):
        """Get database connection context manager"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        try:
            yield conn
        finally:
            conn.close()
    
    # ============ Conversations ============
    
    def create_or_update_conversation(
        self, 
        conversation_uuid: str, 
        user_uuid: str,
        title: Optional[str] = None
    ) -> Conversation:
        """Create or update conversation"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            
            # Check if exists
            cursor.execute(
                'SELECT * FROM conversations WHERE uuid = ?',
                (conversation_uuid,)
            )
            existing = cursor.fetchone()
            
            if existing:
                # Update
                cursor.execute('''
                    UPDATE conversations 
                    SET updated_at = ?, title = COALESCE(?, title)
                    WHERE uuid = ?
                ''', (now, title, conversation_uuid))
            else:
                # Create
                cursor.execute('''
                    INSERT INTO conversations (uuid, user_uuid, title, created_at, updated_at, message_count)
                    VALUES (?, ?, ?, ?, ?, 0)
                ''', (conversation_uuid, user_uuid, title, now, now))
            
            conn.commit()
            
            return self.get_conversation(conversation_uuid)
    
    def get_conversation(self, conversation_uuid: str) -> Optional[Conversation]:
        """Get conversation by UUID"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM conversations WHERE uuid = ?', (conversation_uuid,))
            row = cursor.fetchone()
            
            if row:
                return Conversation(
                    uuid=row['uuid'],
                    user_uuid=row['user_uuid'],
                    title=row['title'],
                    created_at=row['created_at'],
                    updated_at=row['updated_at'],
                    message_count=row['message_count']
                )
            return None
    
    def list_conversations(
        self, 
        user_uuid: str,
        limit: int = 100
    ) -> List[Conversation]:
        """List conversations for a user"""
        with self._get_connection() as conn:
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
                    created_at=row['created_at'],
                    updated_at=row['updated_at'],
                    message_count=row['message_count']
                ))
            
            return conversations

    def delete_conversation(self, conversation_uuid: str):
        """Delete a single conversation and all related messages/artifacts"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Delete artifacts
            cursor.execute('''
                DELETE FROM artifacts 
                WHERE message_uuid IN (SELECT uuid FROM messages WHERE conversation_uuid = ?)
            ''', (conversation_uuid,))

            # Delete messages
            cursor.execute('DELETE FROM messages WHERE conversation_uuid = ?', (conversation_uuid,))

            # Delete conversation
            cursor.execute('DELETE FROM conversations WHERE uuid = ?', (conversation_uuid,))
            conn.commit()

    def delete_all(self, user_uuid: str = None):
        """
        Удаляет ВСЕ данные из базы, игнорируя user_uuid.
        Мы игнорируем аргумент user_uuid, чтобы гарантированно стереть всё.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Отключаем проверку связей, чтобы не возиться с порядком удаления
            cursor.execute("PRAGMA foreign_keys = OFF;")

            # Удаляем ВСЕ строки из таблиц. Без условий WHERE.
            cursor.execute("DELETE FROM artifacts;")
            cursor.execute("DELETE FROM messages;")
            cursor.execute("DELETE FROM conversations;")

            cursor.execute("PRAGMA foreign_keys = ON;")
            conn.commit()

            # Принудительно сжимаем базу, чтобы убедиться, что данные стерты физически
            cursor.execute("VACUUM;")

    # ============ Messages ============

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
        """Create a message"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            cursor.execute('''
                INSERT INTO messages 
                (uuid, conversation_uuid, user_uuid, task_id, prompt, answer, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (message_uuid, conversation_uuid, user_uuid, task_id, prompt, answer, summary, now))

            # Update conversation message count and updated_at
            cursor.execute('''
                UPDATE conversations 
                SET message_count = message_count + 1,
                    updated_at = ?
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
        """Update message answer/summary"""
        with self._get_connection() as conn:
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
                cursor.execute(
                    f'UPDATE messages SET {", ".join(updates)} WHERE uuid = ?',
                    params
                )
                conn.commit()

            return self.get_message(message_uuid)

    def get_message(self, message_uuid: str) -> Optional[Message]:
        """Get message by UUID"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM messages WHERE uuid = ?', (message_uuid,))
            row = cursor.fetchone()

            if row:
                # Get artifacts
                cursor.execute(
                    'SELECT * FROM artifacts WHERE message_uuid = ?',
                    (message_uuid,)
                )
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

    def get_message_by_task_id(self, task_id: str) -> Optional[Message]:
        """Get message by task_id"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT uuid FROM messages WHERE task_id = ?', (task_id,))
            row = cursor.fetchone()

            if row:
                return self.get_message(row['uuid'])
            return None

    def list_messages(
        self,
        conversation_uuid: str,
        limit: int = 100
    ) -> List[Message]:
        """List messages in a conversation"""
        with self._get_connection() as conn:
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

    # ============ Artifacts ============

    def create_artifact(
        self,
        message_uuid: str,
        artifact_type: str,
        name: Optional[str] = None,
        path: Optional[str] = None,
        data: Optional[Dict] = None
    ) -> Artifact:
        """Create an artifact"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            data_json = json.dumps(data) if data else None

            cursor.execute('''
                INSERT INTO artifacts 
                (message_uuid, artifact_type, name, path, data, created_at)
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
        """List artifacts for a message"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM artifacts WHERE message_uuid = ?',
                (message_uuid,)
            )

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

    def hard_reset(self):
        """
        Drop all tables to clear DB completely without deleting the file.
        This avoids 'file is locked' errors.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Сносим таблицы
            cursor.execute("DROP TABLE IF EXISTS artifacts")
            cursor.execute("DROP TABLE IF EXISTS messages")
            cursor.execute("DROP TABLE IF EXISTS conversations")
            conn.commit()

        # Сразу пересоздаем пустую структуру
        self._init_db()