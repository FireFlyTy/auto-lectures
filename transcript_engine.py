import os
import hashlib
import json
import time
import numpy as np
from openai import OpenAI
from dotenv import load_dotenv
from agent.helper import parse_json
import httpx

load_dotenv()


def calculate_file_hash(file_bytes):
    return hashlib.md5(file_bytes).hexdigest()


def process_audio_with_deepgram(audio_bytes, output_path, status_callback=None):
    def update_stage(stage, percent):
        if status_callback:
            status_callback(stage, percent)

    update_stage("Connecting to Deepgram...", 10)

    api_key = os.getenv("DEEPGRAM_KEY")
    client_openai = OpenAI()

    update_stage("Transcribing audio (this may take a few minutes)...", 20)

    # Используем httpx напрямую с большим timeout
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "audio/mpeg",
    }

    params = {
        "model": "nova-2",
        "language": "pt",
        "smart_format": "true",
        "diarize": "true",
        "utterances": "true",
    }

    # Timeout: 10 минут для больших файлов
    timeout = httpx.Timeout(600.0, connect=30.0)

    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            "https://api.deepgram.com/v1/listen",
            headers=headers,
            params=params,
            content=audio_bytes,
        )
        response.raise_for_status()
        data = response.json()

    update_stage("Processing transcription...", 40)

    result = []
    results = data.get("results", {})
    utterances = results.get("utterances", [])

    if utterances:
        current_chunk = None

        for utterance in utterances:
            speaker = str(utterance.get("speaker", 0))
            text = utterance.get("transcript", "")
            start = utterance.get("start", 0)
            end = utterance.get("end", 0)

            if current_chunk and current_chunk["speaker"] == speaker:
                current_chunk["text"] += " " + text
                current_chunk["end"] = end
            else:
                if current_chunk:
                    result.append(current_chunk)
                current_chunk = {
                    "speaker": speaker,
                    "start": start,
                    "end": end,
                    "text": text
                }
        if current_chunk:
            result.append(current_chunk)

    update_stage("AI identifying speakers...", 50)

    # Identify speakers
    prompt = f"Define names and roles of speakers for Transcript:\n{str(result)}\n\n==========\nReturn json {{'speakers': ['id': <id of speaker from transcript>, 'name': <>, 'role': <>]}}"

    try:
        res = client_openai.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="gpt-5.2",
            reasoning_effort='medium'
        )
        speakers_data = parse_json(res.choices[0].message.content)

        update_stage("Applying speaker names...", 60)

        speaker_map = {str(s['id']): s['name'] for s in speakers_data.get('speakers', [])}

        for r in result:
            sp_id = str(r['speaker'])
            r['name'] = speaker_map.get(sp_id, f"Speaker {sp_id}")

    except Exception as e:
        print(f"Speaker identification failed: {e}")
        for r in result:
            r['name'] = f"Speaker {r.get('speaker', '?')}"

    update_stage("Formatting transcript...", 70)

    try:
        prompt = f'''Identify mistakes in the speakers <> transcript connection in the transcript. 
        You have to return the transcript timeframe, the current id, name of speaker and the proposed ones. Provide explanation.
        The most common mistake when the speaker asks themself. 
        Transcript:
        {result}
        ==========
        Return json {{['start': <start time point>, 'end': <start time point>, 
                       'old_speaker': {{"id": <id of current speaker>, "name": <name of current speaker>}}, 
                       'new_speaker': {{"id": <id of proposed speaker>, "name": <name of proposed speaker>}}, 'explanation': <explain the substitution>]}}'''
        res = client_openai.chat.completions.create(messages=[{"role": "user", "content": prompt}],
                                                    model="gpt-5.2", reasoning_effort="medium")
        errors = parse_json(res.choices[0].message.content)

        for err in errors:
            print(err)
            for res in result:
                if res['start'] == err['start'] and res['end'] == err['end']:
                    res['id'] = err['new_speaker']['id']
                    res['name'] = err['new_speaker']['name']
                    break
    except Exception as e:
        print(f"Speaker Correction failed: {e}")


    update_stage("Formatting transcript...", 90)

    f_tr = []
    for r in result:
        st = r['start'] / 60
        ed = r['end'] / 60
        st_h = int(st // 60)
        ed_h = int(ed // 60)
        st_m = int(np.ceil(st - st_h * 60))
        ed_m = int(np.ceil(ed - ed_h * 60))
        start_fmt = f"{st_h:02d}:{st_m:02d}"
        end_fmt = f"{ed_h:02d}:{ed_m:02d}"

        name = r.get('name', r.get('speaker', 'Unknown'))
        text = f"[{start_fmt} - {end_fmt} ({name})]\n {r['text']}\n\n"
        f_tr.append(text)

    final_transcript = '\n'.join(f_tr)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(final_transcript)

    update_stage("Transcript saved!", 80)

    return final_transcript


def analyze_transcript_suggestions(transcript_text: str, status_callback=None) -> list:
    """
    Генерирует ТОЛЬКО ВОПРОСЫ (Prompts), которые пользователь может кликнуть.
    """
    if not transcript_text or len(transcript_text.strip()) < 100:
        print("⚠ Transcript too short for suggestions")
        return []

    if status_callback:
        status_callback("Generating analysis suggestions...", 90)

    client_openai = OpenAI()

    prompt = f'''Analyze this transcript and suggest 5 most useful actions/questions for the user.

Transcript (first 10000 chars):
{transcript_text[:10000]}

Return JSON array with exactly this format:
[
  {{"id": 1, "label": "📝 Summary", "prompt": "Provide a detailed summary of this transcript"}},
  {{"id": 2, "label": "🎯 Key Points", "prompt": "What are the main key points discussed?"}},
  ...
]

Rules:
- "label" should be 2-4 words with an emoji at the start
- "prompt" should be a clear question or action request
- Make suggestions relevant to the transcript content
- Return ONLY valid JSON array, no other text
'''

    try:
        res = client_openai.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="gpt-4.1",
        )
        actions = parse_json(res.choices[0].message.content)

        if isinstance(actions, list) and len(actions) > 0:
            print(f"✓ Generated {len(actions)} suggestions")
            return actions
        else:
            print(f"⚠ Invalid suggestions format: {type(actions)}")
            return []

    except Exception as e:
        print(f"✗ Error generating suggestions: {e}")
        return []