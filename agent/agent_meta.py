from __future__ import annotations
import json

from typing import Any, Tuple
from typing_extensions import TypeVar
from dotenv import load_dotenv
from agent.helper import AgentSession, parse_json

TContext = TypeVar("TContext", default=Any)

# ---- OpenAI Agent imports ----
from agents import (
    Agent,
    SQLiteSession,
    function_tool,
    RunContextWrapper,

)

# Load environment variables
load_dotenv()


# ============================================
# Main Agent and Execution
# ============================================

async def create_agent(
    conversation_id: str = "data-conversation-1",
    transcript: str = ""
) -> Tuple[Agent, SQLiteSession]:
    """Create and configure the data analysis agent"""

    # Create conversation for memory persistence
    conversation = SQLiteSession(conversation_id, db_path="../analyst_memory.db")


    SYSTEM_PROMPT = f'''You have to answer of any question from user if this question relates to the transcript and topics from the transcript
    You have to summarize, provide insights from transcript, use exactly text from transcript as the context.
    
    Answer on the question using the user's request language! 
    The user's request might differ from the language of transcript
    Transcript:
    {transcript}
    '''

    # Create the agent with OpenAI agent framework
    agent = Agent[AgentSession](
        name="Transcript Agent",
        model="gpt-4.1",
        instructions=SYSTEM_PROMPT,
    )

    return agent, conversation