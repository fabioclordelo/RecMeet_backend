from flask import Flask, request, jsonify
from utils.transcriber import transcribe_audio
from utils.summarizer import summarize_transcript
from google.cloud import storage
import os
import json
import time
from datetime import datetime
import uuid
from threading import Thread

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Load GCS bucket name
GCS_BUCKET = os.getenv("GCS_BUCKET")
storage_client = storage.Client()

# Increase max request size to 100MB
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

@app.route('/')
def index():
    return "✅ RecMeet backend is running."

def async_process_audio(filepath, blob_path):
    try:
        raw_transcript, detected_langs = transcribe_audio(filepath)
        cleaned_transcript, summary = summarize_transcript(raw_transcript, detected_langs)

        result = {
            "languages": detected_langs,
            "transcript": cleaned_transcript,
            "summary": summary
        }

        bucket = storage_client.bucket(GCS_BUCKET)
        blob = bucket.blob(blob_path)
        blob.upload_from_string(
            data=json.dumps(result, indent=2, ensure_ascii=False),
            content_type='application/json'
        )

        print(f"✅ Background processing done → {blob_path}")

    except Exception as e:
        print(f"❌ Error in async processing: {e}")

@app.route('/upload', methods=['POST'])
def upload():
    try:
        file = request.files.get('audio')
        if not file:
            return jsonify({"error": "Missing 'audio' file in request"}), 400

        unique_id = uuid.uuid4().hex
        audio_filename = f"{unique_id}.m4a"
        local_path = os.path.join(UPLOAD_FOLDER, audio_filename)
        file.save(local_path)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        json_filename = f"meeting_{timestamp}.json"
        blob_path = f"meetings/{json_filename}"

        thread = Thread(target=async_process_audio, args=(local_path, blob_path))
        thread.start()

        return jsonify({
            "status": "accepted",
            "message": "Audio received and processing started.",
            "filename": json_filename
        }), 202

    except Exception as e:
        print(f"❌ Upload error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/status/<filename>', methods=['GET'])
def check_status(filename):
    try:
        blob = storage_client.bucket(GCS_BUCKET).blob(f"meetings/{filename}")
        if blob.exists():
            return jsonify({"status": "complete"}), 200
        return jsonify({"status": "processing"}), 202
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/list', methods=['GET'])
def list_meetings():
    try:
        bucket = storage_client.bucket(GCS_BUCKET)
        blobs = bucket.list_blobs(prefix="meetings/")
        meetings = []

        for blob in blobs:
            if blob.name.endswith(".json") and "meeting_" in blob.name:
                content = blob.download_as_text()
                data = json.loads(content)
                transcript = data.get("transcript", "")
                summary = data.get("summary", "")
                filename = os.path.basename(blob.name)

                if filename.startswith("meeting_") and filename.endswith(".json"):
                    timestamp_str = filename[len("meeting_"):-len(".json")]
                    try:
                        dt = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S")
                        display_name = dt.strftime("%m/%d/%Y (%H:%M:%S)") + " Meeting"
                        meetings.append({
                            "filename": filename,
                            "displayName": display_name,
                            "transcript": transcript,
                            "summary": summary
                        })
                    except Exception as e:
                        print(f"⚠️ Timestamp parse error in {filename}: {e}")
                else:
                    print(f"⚠️ Skipped invalid filename: {blob.name}")

        meetings.sort(key=lambda x: x["displayName"], reverse=True)
        return jsonify(meetings)

    except Exception as e:
        print(f"❌ List error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    pass  # Used by gunicorn
