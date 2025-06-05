from flask import Flask, request, jsonify, send_from_directory
from utils.transcriber import transcribe_audio
from utils.summarizer import summarize_transcript
import os
import json
from datetime import datetime

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route('/')
def index():
    return send_from_directory('../frontend', 'index.html')

@app.route('/upload', methods=['POST'])
def upload():
    file = request.files['audio']
    filepath = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(filepath)

    transcript, detected_langs = transcribe_audio(filepath)
    summary = summarize_transcript(transcript, detected_langs)

    result = {
        "languages": detected_langs,
        "transcript": transcript,
        "summary": summary
    }

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    json_filename = f"meeting_{timestamp}.json"
    json_path = os.path.join(UPLOAD_FOLDER, json_filename)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True)
