from typing import Any, Dict, Optional, List, Tuple
import os
import json
import ast

def parse_json(answer):
    try:
        answer = json.loads(answer.split("```json")[-1].split("```")[0])
    except:
        try:
            answer = ast.literal_eval(answer.split("```json")[-1].split("```")[0])
        except:
            pass
    return answer

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

        if not os.path.exists("../conversations"):
            os.makedirs("../conversations")

        with open(f"./conversations/{self.conversation_id}", "w") as f:
            dump = {
                "transcript": self.transcript,
                "answers": self.answers,
            }

            json.dump(dump, f)

    def load(self):
        path = f"./conversations/{self.conversation_id}"
        if not os.path.exists(path):
            return

        with open(f"./conversations/{self.conversation_id}", "r") as f:
            dump = json.load(f)
            self.answers = dump.get("answers", [])
            self.transcript = dump.get("transcript", "")

    def start_new_turn(self):
        """Call this at the start of each user message"""
        self.answers = []
        self.transcript = ""

    def add_answer(self, answer):
        self.answers.append(answer)