from typing import Any, Dict, Optional, List, Tuple
import os
import json
import ast
from pathlib import Path


def parse_json(answer):
    try:
        answer = json.loads(answer.split("```json")[-1].split("```")[0])
    except:
        try:
            answer = ast.literal_eval(answer.split("```json")[-1].split("```")[0])
        except:
            pass
    return answer


# Определяем BASE_DIR один раз
_BASE_DIR = Path(__file__).resolve().parent.parent
_CONV_DIR = _BASE_DIR / "conversations"


class AgentSession:
    conversation_id: str
    message_id: str

    def __init__(
            self,
            conversation_id: str,
            message_id: str
    ):
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.answers = []
        self.transcript = ""

    def save(self):
        # Используем абсолютный путь
        if not _CONV_DIR.exists():
            _CONV_DIR.mkdir(parents=True, exist_ok=True)

        filepath = _CONV_DIR / self.conversation_id

        with open(filepath, "w", encoding="utf-8") as f:
            dump = {
                "transcript": self.transcript,
                "answers": self.answers,
            }
            json.dump(dump, f, ensure_ascii=False)

    def load(self):
        filepath = _CONV_DIR / self.conversation_id

        if not filepath.exists():
            return

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                dump = json.load(f)
                self.answers = dump.get("answers", [])
                self.transcript = dump.get("transcript", "")
        except Exception as e:
            print(f"Error loading session {self.conversation_id}: {e}")
            self.answers = []
            self.transcript = ""

    def start_new_turn(self):
        """Call this at the start of each user message"""
        self.answers = []
        self.transcript = ""

    def add_answer(self, answer):
        self.answers.append(answer)