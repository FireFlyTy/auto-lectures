import asyncio
import io
import math
from pydub import AudioSegment
from openai import AsyncOpenAI
from dotenv import load_dotenv
load_dotenv()

client = AsyncOpenAI()

async def transcribe_segment(chunk, index, semaphore):
    async with semaphore:
        try:
            buffer = io.BytesIO()
            chunk.export(buffer, format="mp3")
            buffer.seek(0)
            buffer.name = f"part_{index}.mp3"

            print(f"--> Sending Chunk {index}...")

            transcription = await client.audio.transcriptions.create(
                model="whisper-1",
                file=buffer,
                response_format='verbose_json'
            )

            print(f"<-- Received Chunk {index}")
            return transcription.text

        except Exception as e:
            print(f"Error in Chunk {index}: {e}")
            return ""


async def get_transcript(fname = "audio.mp3"):
    print("Loading audio...")
    audio = AudioSegment.from_file(fname)

    split_len = 15 * 60 * 1000  # 10 minutes
    total_duration = len(audio)
    total_chunks = math.ceil(total_duration / split_len)

    chunks = []
    for i in range(total_chunks):
        start = i * split_len
        end = min((i + 1) * split_len, total_duration)
        chunks.append(audio[start:end])

    sem = asyncio.Semaphore(5)

    tasks = []
    for i, chunk in enumerate(chunks):
        task = transcribe_segment(chunk, i, sem)
        tasks.append(task)

    print(f"Starting async transcription for {len(tasks)} parts...")

    results = await asyncio.gather(*tasks)

    full_text = " ".join(results)

    return full_text

