import os
from dotenv import load_dotenv
import json
import numpy as np
from openai import OpenAI
import pandas as pd
from agent.helper import parse_json



load_dotenv()

API_KEY = os.getenv("DEEPGRAM_KEY")
AUDIO_FILE = "audio.mp3"

client_openai = OpenAI()

from deepgram import DeepgramClient

# Initialize client
client = DeepgramClient(api_key=API_KEY)

# Read audio file
with open(AUDIO_FILE, "rb") as file:
    audio_data = file.read()

response = client.listen.v1.media.transcribe_file(
    request=audio_data,  # bytes diretamente
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
        speaker = utterance.speaker
        text = utterance.transcript
        start = utterance.start
        end = utterance.end

        # If same speaker as current chunk, merge text and update end time
        if current_chunk and current_chunk["speaker"] == speaker:
            current_chunk["text"] += " " + text
            current_chunk["end"] = end
        else:
            # Save previous chunk if exists
            if current_chunk:
                result.append(current_chunk)

            # Start new chunk
            current_chunk = {
                "speaker": speaker,
                "start": start,
                "end": end,
                "text": text
            }

    # Don't forget the last chunk
    if current_chunk:
        result.append(current_chunk)

#print(json.dumps(result, indent=2, ensure_ascii=False))
json.dump(result, open("../experiments/transcript_json.json", "w"))

prompt = f"Define names and roles of speakers for Transcript:\n{result}\n\n==========\nReturn json {{'speakers': ['id': <id of speaker from transcript>, 'name': <>, 'role': <>]}}"
res = client_openai.chat.completions.create(messages=[{"role": "user", "content": prompt}],
                                     model="gpt-5.2", reasoning_effort="medium")

speakers = parse_json(res.choices[0].message.content)
speakers = pd.DataFrame(speakers['speakers']).set_index('id').to_dict('index')
for res in result:
    res['name'] = speakers[res['speaker']]['name']

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

f_tr = []
for r in result:
    st = r['start'] / 60
    ed = r['end'] / 60
    st_h = int(st // 60)
    ed_h = int(ed // 60)
    st_m = int(np.ceil(st - st_h*60))
    ed_m = int(np.ceil(ed - ed_h * 60))
    start = f"{st_h:02d}:{st_m:02d}"
    end = f"{ed_h:02d}:{ed_m:02d}"
    text = f"[{start} - {end} ({r['name']})]\n {r['text']}\n\n"
    f_tr.append(text)
final_transcript = '\n'.join(f_tr)

with open("../transcript_deepgram.txt", "w") as f:
    f.write(final_transcript)
