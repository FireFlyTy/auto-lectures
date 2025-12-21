#!/usr/bin/env python3
"""
Interactive CLI for Transcript Agent
Usage: python transcript_cli.py [--url URL] [--user USER] [--conversation CONV_ID] [--transcript PATH]
"""

import sys
import os
import uuid
from typing import Optional, List, Dict, Any
from datetime import datetime
from transcript_client import TranscriptClient


class Colors:
    """ANSI color codes for terminal output - disabled"""
    BLACK = ''
    RED = ''
    GREEN = ''
    YELLOW = ''
    BLUE = ''
    MAGENTA = ''
    CYAN = ''
    WHITE = ''
    BOLD = ''
    DIM = ''
    ITALIC = ''
    UNDERLINE = ''
    BG_BLUE = ''
    BG_GREEN = ''
    BG_YELLOW = ''
    BG_RED = ''
    END = ''
    RESET = ''


class Formatter:
    """Text formatting utilities - simplified"""

    @staticmethod
    def box(text: str, title: str = "", color: str = '', width: int = 80) -> str:
        """Create a box around text - simplified"""
        lines = text.split('\n')

        # Top border
        if title:
            top = f"={'=' * 78}"
            content = [f" {title.upper()} ", top]
        else:
            top = f"={'=' * 78}"
            content = [top]

        # Content
        for line in lines:
            content.append(line)

        # Bottom border
        bottom = f"={'=' * 78}"
        content.append(bottom)

        return '\n'.join(content)

    @staticmethod
    def section(title: str, color: str = '') -> str:
        """Create a section header"""
        return f"\n> {title}"

    @staticmethod
    def bullet(text: str, symbol: str = "•") -> str:
        """Create a bullet point"""
        return f"  • {text}"

    @staticmethod
    def success(text: str) -> str:
        """Format success message"""
        return f"[OK] {text}"

    @staticmethod
    def error(text: str) -> str:
        """Format error message"""
        return f"[ERROR] {text}"

    @staticmethod
    def warning(text: str) -> str:
        """Format warning message"""
        return f"[WARNING] {text}"

    @staticmethod
    def info(text: str) -> str:
        """Format info message"""
        return f"[INFO] {text}"


class InteractiveCLI:
    """Interactive command-line interface for the transcript agent"""

    def __init__(
            self,
            base_url: str = "http://localhost:8000",
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            transcript_path: Optional[str] = None,
            use_streaming: bool = False
    ):
        self.client = TranscriptClient(base_url)
        self.conversation_id = conversation_id
        self.user_id = user_id or str(uuid.uuid4())
        self.transcript_path = transcript_path
        self.question_count = 0
        self.base_url = base_url
        self.history: List[Dict[str, Any]] = []
        self.use_streaming = use_streaming

    def clear_screen(self):
        """Clear the terminal screen"""
        os.system('clear' if os.name != 'nt' else 'cls')

    def print_header(self):
        """Print welcome header"""
        header = """
================================================================================

              TRANSCRIPT AGENT - INTERACTIVE CONSOLE                  

                    Question Answering over Transcripts                               

================================================================================
"""
        print(header)

    def print_help(self):
        """Print help information"""
        help_text = f"""
> COMMANDS

  • help       Show this help message
  • status     Show current session status
  • ids        Show current IDs (for easy copying)
  • new        Start a new conversation (auto-generated IDs)
  • newid      Start a new conversation with custom IDs
  • load       Load transcript for current conversation
  • setuser    Change user ID for current session
  • streaming  Toggle streaming mode (currently: {'ON' if self.use_streaming else 'OFF'})
  • history    Show conversation history
  • clear      Clear the screen
  • quit       Exit the console (also: exit, q)

> USAGE

Simply type your question and press Enter to get answers from the transcript.

Examples:
  • What was discussed in the meeting?
  • Summarize the main points
  • Who mentioned the project deadline?

Streaming mode: Real-time response vs polling
"""
        print(help_text)

    def check_server(self) -> bool:
        """Check if server is running"""
        try:
            health = self.client.health_check()

            status_box = f"""{Formatter.success("Server is healthy and ready")}

Server Details:
  {Formatter.bullet(f"URL: {self.base_url}")}
  {Formatter.bullet(f"Active Conversations: {health['active_conversations']}")}
  {Formatter.bullet(f"Active Tasks: {health['active_tasks']}")}
"""
            print(Formatter.box(status_box, "SERVER STATUS"))
            return True

        except Exception as e:
            error_box = f"""{Formatter.error("Cannot connect to server")}

Details:
  {Formatter.bullet(f"URL: {self.base_url}")}
  {Formatter.bullet(f"Error: {str(e)}")}

Please start the server first:
  python transcript_service.py
"""
            print(Formatter.box(error_box, "CONNECTION ERROR"))
            return False

    def start_conversation(
            self,
            custom_user_id: Optional[str] = None,
            custom_conv_id: Optional[str] = None,
            transcript_path: Optional[str] = None
    ):
        """Start a new conversation"""
        try:
            # Use custom IDs if provided, otherwise generate
            self.conversation_id = custom_conv_id or str(uuid.uuid4())
            if custom_user_id:
                self.user_id = custom_user_id

            # Reset question counter
            self.question_count = 0
            self.history = []

            # Load transcript if provided
            if transcript_path:
                self.transcript_path = transcript_path
                self.load_transcript(transcript_path)

            # Show conversation details
            conv_info = f"""{Formatter.success("New conversation started")}

Session Details:
  {Formatter.bullet(f"Conversation ID: {self.conversation_id}")}
  {Formatter.bullet(f"User ID: {self.user_id}")}
  {Formatter.bullet(f"Transcript: {self.transcript_path if self.transcript_path else 'Not loaded'}")}
  {Formatter.bullet(f"Status: ready")}
"""
            print(Formatter.box(conv_info, "NEW CONVERSATION"))

        except Exception as e:
            print(Formatter.error(f"Failed to start conversation: {e}"))

    def load_transcript(self, transcript_path: str):
        """Load transcript for current conversation"""
        if not self.conversation_id:
            print(Formatter.error("No active conversation. Start one with 'new' command"))
            return

        try:
            result = self.client.load_transcript(
                self.conversation_id,
                transcript_path
            )

            load_info = f"""{Formatter.success("Transcript loaded successfully")}

Details:
  {Formatter.bullet(f"Conversation ID: {self.conversation_id}")}
  {Formatter.bullet(f"Transcript Path: {transcript_path}")}
  {Formatter.bullet(f"Transcript Length: {result['transcript_length']} characters")}
"""
            print(Formatter.box(load_info, "TRANSCRIPT LOADED"))
            self.transcript_path = transcript_path

        except Exception as e:
            print(Formatter.error(f"Failed to load transcript: {e}"))

    def show_status(self):
        """Show current session status"""
        if not self.conversation_id:
            print(Formatter.warning("No active conversation"))
            return

        status_info = f"""
Conversation Status:
  {Formatter.bullet(f"ID: {self.conversation_id}")}
  {Formatter.bullet(f"User: {self.user_id}")}
  {Formatter.bullet(f"Transcript: {self.transcript_path if self.transcript_path else 'Not loaded'}")}
  {Formatter.bullet(f"Questions Asked: {self.question_count}")}
  {Formatter.bullet(f"Status: active")}
"""
        print(Formatter.box(status_info, "SESSION STATUS"))

    def show_history(self):
        """Show conversation history"""
        if not self.history:
            print(Formatter.warning("No conversation history yet"))
            return

        history_text = f"""
Total Questions: {len(self.history)}

"""
        for i, item in enumerate(self.history, 1):
            history_text += f"\n{i}. Q: {item['question']}\n"
            history_text += f"   A: {item['answer'][:100]}{'...' if len(item['answer']) > 100 else ''}\n"
            history_text += f"   Time: {item['timestamp']}\n"

        print(Formatter.box(history_text, "CONVERSATION HISTORY"))

    def ask_question(self, question: str):
        """Ask a question to the transcript agent"""
        if not self.conversation_id:
            print(Formatter.error("No active conversation. Start one with 'new' command"))
            return

        if not self.transcript_path:
            print(Formatter.warning("No transcript loaded. Use 'load' command to load a transcript"))
            return

        try:
            self.question_count += 1
            print(f"\n[Processing question #{self.question_count}...]")

            if self.use_streaming:
                # Use streaming mode
                print(f"\n{Formatter.info('Streaming mode enabled')}\n")
                answer_text = ""

                for event in self.client.ask_question_streaming(
                        conversation_id=self.conversation_id,
                        user_id=self.user_id,
                        prompt=question,
                        transcript_path=self.transcript_path
                ):
                    event_type = event.get('type')

                    if event_type == 'delta':
                        # Print delta in real-time
                        delta = event.get('delta', '')
                        print(delta, end='', flush=True)
                        answer_text = event.get('accumulated', answer_text)

                    elif event_type == 'done':
                        answer_text = event.get('text', answer_text)
                        print()  # New line after streaming

                    elif event_type == 'error':
                        err = event.get('error')
                        print(f"\n{Formatter.error(f'Error: {err}')}")
                        return

                    elif event_type == 'end':
                        break

                if not answer_text:
                    print(Formatter.warning("No response received"))
                    return

            else:
                # Use non-streaming mode (polling)
                result = self.client.ask_question(
                    conversation_id=self.conversation_id,
                    user_id=self.user_id,
                    prompt=question,
                    transcript_path=self.transcript_path,
                    show_progress=True
                )

                # Extract answer from result
                answer_text = result.get('result', {}).get('text', str(result))

            # Format and display answer (for non-streaming or final summary)
            if not self.use_streaming:
                formatted = f"""
Question: {question}

Answer: {answer_text}
"""
                print(Formatter.box(formatted, f"ANSWER #{self.question_count}", width=80))

            # Store in history
            self.history.append({
                'question': question,
                'answer': answer_text,
                'timestamp': datetime.now().isoformat()
            })

            print()

        except Exception as e:
            print(Formatter.error(f"Error: {e}"))
            print()

    def run(self):
        """Run the interactive console"""
        self.clear_screen()
        self.print_header()

        # Check server
        if not self.check_server():
            return

        # Print help
        self.print_help()

        # Start initial conversation if IDs provided
        if self.conversation_id or self.transcript_path:
            self.start_conversation(
                custom_conv_id=self.conversation_id,
                transcript_path=self.transcript_path
            )
        else:
            print(Formatter.info("Use 'new' command to start a conversation"))

        try:
            while True:
                try:
                    # Get input with styled prompt
                    prompt_text = f"\nQ{self.question_count + 1}> "
                    question = input(prompt_text).strip()

                    # Handle empty input
                    if not question:
                        continue

                    # Handle commands
                    cmd = question.lower()

                    if cmd in ['quit', 'exit', 'q']:
                        break

                    elif cmd == 'help':
                        self.print_help()

                    elif cmd == 'status':
                        self.show_status()

                    elif cmd == 'ids':
                        # Show IDs in compact format for easy copying
                        if self.conversation_id:
                            print(f"\nCurrent Session IDs:\n")
                            print(f"Conversation ID:")
                            print(f"  {self.conversation_id}")
                            print(f"\nUser ID:")
                            print(f"  {self.user_id}\n")
                        else:
                            print(Formatter.warning("No active conversation"))

                    elif cmd == 'new':
                        self.start_conversation()

                    elif cmd == 'newid':
                        # Start conversation with custom IDs
                        print(f"\nCreate New Session with Custom IDs")
                        print(f"Press Enter to auto-generate\n")

                        conv_id = input(f"  Conversation ID: ").strip()
                        user_id = input(f"  User ID: ").strip()
                        transcript = input(f"  Transcript Path: ").strip()

                        self.start_conversation(
                            custom_user_id=user_id if user_id else None,
                            custom_conv_id=conv_id if conv_id else None,
                            transcript_path=transcript if transcript else None
                        )

                    elif cmd == 'load':
                        # Load transcript
                        print(f"\nLoad Transcript")
                        transcript = input(f"  Transcript Path: ").strip()

                        if transcript:
                            self.load_transcript(transcript)
                        else:
                            print(Formatter.warning("No path provided"))

                    elif cmd == 'setuser':
                        # Change user ID
                        print(f"\nChange User ID")
                        print(f"Current: {self.user_id}\n")

                        new_user_id = input(f"  New User ID: ").strip()

                        if new_user_id:
                            old_user_id = self.user_id
                            self.user_id = new_user_id
                            print(Formatter.box(
                                f"{Formatter.success('User ID updated')}\n\n"
                                f"  Old: {old_user_id}\n"
                                f"  New: {self.user_id}",
                                "USER ID CHANGED"
                            ))
                        else:
                            print(Formatter.warning("User ID not changed"))

                    elif cmd == 'history':
                        self.show_history()

                    elif cmd == 'streaming':
                        # Toggle streaming mode
                        self.use_streaming = not self.use_streaming
                        mode = "enabled" if self.use_streaming else "disabled"
                        print(Formatter.box(
                            f"{Formatter.success('Streaming mode toggled')}\n\n"
                            f"  Mode: {mode}",
                            "STREAMING MODE"
                        ))

                    elif cmd == 'clear':
                        self.clear_screen()
                        self.print_header()

                    else:
                        # Regular question
                        self.ask_question(question)

                except KeyboardInterrupt:
                    print(f"\n\n{Formatter.warning('Use quit to exit cleanly')}\n")
                    continue

                except EOFError:
                    break

        finally:
            # Cleanup and goodbye
            goodbye = f"""
{Formatter.success("Session ended successfully")}

Session Summary:
  {Formatter.bullet(f"Questions Asked: {self.question_count}")}
  {Formatter.bullet(f"Conversation ID: {self.conversation_id if self.conversation_id else 'N/A'}")}
  {Formatter.bullet(f"User ID: {self.user_id}")}

Thank you for using Transcript Agent!
"""
            print(f"\n{Formatter.box(goodbye, 'GOODBYE')}\n")


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Interactive CLI for Transcript Agent',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python transcript_cli.py
  python transcript_cli.py --url http://localhost:8000
  python transcript_cli.py --conversation conv-123 --transcript transcripts/meeting.txt
        """
    )

    parser.add_argument(
        '--url',
        default='http://localhost:8000',
        help='Base URL of the agent service (default: http://localhost:8000)'
    )

    parser.add_argument(
        '--user',
        help='User ID for the conversation (auto-generated if not provided)'
    )

    parser.add_argument(
        '--conversation',
        help='Conversation ID (auto-generated if not provided)'
    )

    parser.add_argument(
        '--transcript',
        help='Path to transcript file'
    )

    parser.add_argument(
        '--stream',
        action='store_true',
        help='Enable streaming mode for responses'
    )

    args = parser.parse_args()

    try:
        cli = InteractiveCLI(
            base_url=args.url,
            user_id=args.user,
            conversation_id=args.conversation,
            transcript_path=args.transcript,
            use_streaming=args.stream
        )
        cli.run()
    except Exception as e:
        print(f"\n[ERROR] Fatal error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()