from flask import Flask, request, jsonify
from utils.transcriber import transcribe_audio
from utils.summarizer import summarize_transcript
import os
import json
import time
from datetime import datetime
import uuid

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Increase max request size to 100MB
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

@app.route('/')
def index():
    return "✅ RecMeet backend is running."

@app.route('/upload', methods=['POST'])
def upload():
    start = time.time()
    try:
        # Ensure file is present
        file = request.files.get('audio')
        if not file:
            return jsonify({"error": "Missing 'audio' file in request"}), 400

        # Save file
        unique_name = f"{uuid.uuid4()}.wav"
        filepath = os.path.join(UPLOAD_FOLDER, unique_name)
        file.save(filepath)

        print(f"📥 Received file: {file.filename} → {filepath}")

        # Transcribe + summarize
        raw_transcript, detected_langs = transcribe_audio(filepath)
        cleaned_transcript, summary = summarize_transcript(raw_transcript, detected_langs)

        # Prepare result
        result = {
            "languages": detected_langs,
            "transcript": cleaned_transcript,
            "summary": summary
        }

        # Save result JSON
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        json_filename = f"meeting_{timestamp}.json"
        json_path = os.path.join(UPLOAD_FOLDER, json_filename)

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        duration = time.time() - start
        print(f"✅ Process completed in {duration:.2f} seconds")

        return jsonify(result)

    except Exception as e:
        print(f"❌ Error during upload processing: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/list', methods=['GET'])
def list_meetings():
    meetings = []

    for filename in os.listdir(UPLOAD_FOLDER):
        if filename.endswith(".json") and filename.startswith("meeting_"):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = json.load(f)
                    transcript = content.get("transcript", "")
                    summary = content.get("summary", "")
                    timestamp_str = filename.replace("meeting_", "").replace(".json", "")
                    dt = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S")
                    display_name = dt.strftime("%m/%d/%Y (%H:%M:%S)") + " Meeting"
                    meetings.append({
                        "filename": filename,
                        "displayName": display_name,
                        "transcript": transcript,
                        "summary": summary
                    })
            except Exception as e:
                print(f"Error reading {filename}: {e}")

    # sort by datetime descending
    meetings.sort(key=lambda x: x["displayName"], reverse=True)
    return jsonify(meetings)

if __name__ == '__main__':
    pass  # Required for gunicorn