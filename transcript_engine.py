import os
import hashlib
import json
import time
import numpy as np
from deepgram import DeepgramClient
from openai import OpenAI
from dotenv import load_dotenv
from agent.helper import parse_json

load_dotenv()


def calculate_file_hash(file_bytes):
    return hashlib.md5(file_bytes).hexdigest()


def process_audio_with_deepgram(audio_bytes, output_path, status_callback=None):
    def update_stage(stage, percent):
        if status_callback:
            status_callback(stage, percent)

    update_stage("Connecting to Deepgram...", 10)

    client_deepgram = DeepgramClient(api_key=os.getenv("DEEPGRAM_KEY"))
    client_openai = OpenAI()

    update_stage("Transcribing audio...", 20)

    # Отправляем raw bytes
    response = client_deepgram.listen.v1.media.transcribe_file(
        request=audio_bytes,
        model="nova-2",
        language="pt",
        smart_format=True,
        diarize=True,
        utterances=True
    )

    result = []
    if hasattr(response.results, 'utterances') and response.results.utterances:
        current_chunk = None
        for utterance in response.results.utterances:
            speaker = str(utterance.speaker)
            text = utterance.transcript
            start = utterance.start
            end = utterance.end

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

    update_stage("AI identifying speakers...", 40)

    prompt = f"Define names and roles of speakers for Transcript:\n{str(result)[:15000]}\n\n==========\nReturn json {{'speakers': ['id': <id of speaker from transcript>, 'name': <>, 'role': <>]}}"

    try:
        res = client_openai.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="gpt-5.2",
            reasoning_effort='medium'
        )
        speakers_data = parse_json(res.choices[0].message.content)

        update_stage("Applying speaker corrections...", 60)

        speaker_map = {str(s['id']): s['name'] for s in speakers_data.get('speakers', [])}

        for r in result:
            sp_id = str(r['speaker'])
            r['name'] = speaker_map.get(sp_id, f"Speaker {sp_id}")

    except Exception as e:
        print(f"Speaker identification failed: {e}")
        for r in result:
            r['name'] = f"Speaker {r.get('speaker', '?')}"

    update_stage("Formatting transcript...", 70)

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

    return final_transcript


def analyze_transcript_suggestions(transcript_text: str, status_callback=None) -> list:
    """
    Генерирует ТОЛЬКО ВОПРОСЫ (Prompts), которые пользователь может кликнуть.
    """
    if status_callback:
        status_callback("Generating analysis chips...", 90)

    client_openai = OpenAI()

    prompt = f'''Get top 5 actions that relate to the transcript and will be interested to user:
    {transcript_text}
    =========
    Return json (list of dict) [{{'id': int (start with 1), 
                                  "label": <Up to 3 words text - name of action. Starts with corresponded icon>,
                                  "prompt": <full text of action - what to do>}}]'''


    res = client_openai.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="gpt-5.2",
        reasoning_effort='medium',
    )
    actions = parse_json(res.choices[0].message.content)


    return actions