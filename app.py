from flask import Flask, request, jsonify, send_from_directory
from utils.transcriber import transcribe_audio
from utils.summarizer import summarize_transcript
import os
import json
from datetime import datetime
import time

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Increase Flask file upload limit (e.g., 100 MB)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

@app.route('/')
def index():

@app.route('/upload', methods=['POST'])
def upload():
    start = time.time()
    file = request.files['audio']
    filepath = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(filepath)

    raw_transcript, detected_langs = transcribe_audio(filepath)
    cleaned_transcript, summary = summarize_transcript(raw_transcript, detected_langs)

    result = {
        "languages": detected_langs,
        "transcript": cleaned_transcript,
        "summary": summary
    }

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    json_filename = f"meeting_{timestamp}.json"
    json_path = os.path.join(UPLOAD_FOLDER, json_filename)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    duration = time.time() - start
    print(f"✅ Process completed in {duration:.2f} seconds")

    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True)