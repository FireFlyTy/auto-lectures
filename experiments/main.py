from dotenv import load_dotenv
from openai import OpenAI
from pydub import AudioSegment
import io
import math
from tqdm import tqdm
from process_audio import get_transcript
import asyncio
import time
import json, ast
import nltk
from nltk.tokenize import sent_tokenize
# nltk.download('punkt_tab')
# nltk.download('punkt')

def parse_json(answer):
    try:
        answer = json.loads(answer.split("```json")[-1].split("```")[0])
    except:
        try:
            answer = ast.literal_eval(answer.split("```json")[-1].split("```")[0])
        except:
            pass
    return answer

load_dotenv()

start_time = time.perf_counter()
transcript = asyncio.run(get_transcript())
end_time = time.perf_counter()
elapsed_time = end_time - start_time
print(f"Execution time: {elapsed_time:.4f} seconds")

with open("transcript.txt", "r") as f:
    tr_text = f.read()

client = OpenAI()

prompt = f"Define the number of speakers for Transcript:\n{transcript}\n\n==========\nReturn json {{'speakers': ['name': <>, 'role': <>, 'contribution': <>]}}"
res = client.chat.completions.create(messages=[{"role": "user", "content": prompt}],
                                     model="gpt-5.2", reasoning_effort="medium")

speakers = parse_json(res.choices[0].message.content)

sentences = sent_tokenize(transcript)

numbered_text = ""
for i, sent in enumerate(sentences):
    numbered_text += f"[{i}] {sent}\n"

prompt = f'''You are a diarization assistant.
I will provide a text split into numbered sentences (IDs).
Your task is to group these sentences by speaker based on the context and flow of conversation.
Speakers: {speakers['speakers']}
Rules:
1. DO NOT rewrite the text. Return ONLY a JSON list.
2. Group consecutive sentences by the same speaker into blocks.
3. The format must be: {{"speaker": "Name", "start_id": <int>, "end_id": <int>}}
4. Cover ALL IDs from 0 to {len(sentences)-1}.
Input Text:
{numbered_text}
Response example (JSON only):
[
  {{"speaker": "Иван", "start_id": 0, "end_id": 3}},
  {{"speaker": "Мария", "start_id": 4, "end_id": 4}},
  {{"speaker": "Иван", "start_id": 5, "end_id": 10}}
]'''
res = client.chat.completions.create(messages=[{"role": "user", "content": prompt}],
                                     model="gpt-5.2", reasoning_effort="medium")
new_transcript = parse_json(res.choices[0].message.content)

prompt = f"Define the key-point for each speaker form transcript. Speakers are {speakers['speakers']}. Transcript :\n{transcript}\n\n==========\nReturn json {{'key_points': ['phrase': <first 5 words of speaker's phrase>, 'speaker': <speaker name>]}}"
res = client.chat.completions.create(messages=[{"role": "user", "content": prompt}],
                                     model="gpt-5.2", reasoning_effort="medium")
key_points = parse_json(res.choices[0].message.content)



audio_file = AudioSegment.from_file("../agent/audio.mp3")
split_len = 300 * 1000
total_chunks = math.ceil(len(audio_file) / split_len)
for i in tqdm(range(2,total_chunks)):
    start_time = i * split_len
    end_time = (i + 1) * split_len

    chunk = audio_file[start_time:end_time]
    buffer = io.BytesIO()
    chunk.export(buffer, format="mp3")
    buffer.seek(0)
    buffer.name = "chunk.mp3"

    transcription = client.audio.transcriptions.create(
        model="whisper-1",
        file=buffer
    )

    tr_text = f"{tr_text}\n{transcription.text}"

print(tr_text)