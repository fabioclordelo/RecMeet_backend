from flask import Flask, request, jsonify
from utils.transcriber import transcribe_audio
from utils.summarizer import summarize_transcript
from google.cloud import storage
import os
import json
import time
from datetime import datetime
import uuid

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Load GCS bucket name
GCS_BUCKET = os.getenv("GCS_BUCKET")

# Init storage client
storage_client = storage.Client()

# Increase max request size to 100MB
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

@app.route('/')
def index():
    return "✅ RecMeet backend is running."

@app.route('/upload', methods=['POST'])
def upload():
    start = time.time()
    try:
        file = request.files.get('audio')
        if not file:
            return jsonify({"error": "Missing 'audio' file in request"}), 400

        # Save audio temporarily
        unique_name = f"{uuid.uuid4()}.wav"
        local_path = os.path.join(UPLOAD_FOLDER, unique_name)
        file.save(local_path)
        print(f"📥 Received file: {file.filename} → {local_path}")

        # Transcribe and summarize
        raw_transcript, detected_langs = transcribe_audio(local_path)
        cleaned_transcript, summary = summarize_transcript(raw_transcript, detected_langs)

        result = {
            "languages": detected_langs,
            "transcript": cleaned_transcript,
            "summary": summary
        }

        # Save to GCS
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        json_filename = f"meeting_{timestamp}.json"
        blob_path = f"meetings/{json_filename}"
        bucket = storage_client.bucket(GCS_BUCKET)
        blob = bucket.blob(blob_path)
        blob.upload_from_string(
            data=json.dumps(result, indent=2, ensure_ascii=False),
            content_type='application/json'
        )

        print(f"✅ Uploaded to GCS: {blob_path}")
        duration = time.time() - start
        print(f"✅ Process completed in {duration:.2f} seconds")

        return jsonify(result)

    except Exception as e:
        print(f"❌ Error during upload processing: {e}")
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
                timestamp_str = filename.replace("meeting_", "").replace(".json", "")
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
                    print(f"⚠️ Could not parse timestamp in {filename}: {e}")

        meetings.sort(key=lambda x: x["displayName"], reverse=True)
        return jsonify(meetings)

    except Exception as e:
        print(f"❌ Error during listing: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    pass  # Used by gunicorn