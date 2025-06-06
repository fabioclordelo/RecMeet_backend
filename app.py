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
LOCAL_JSON_FOLDER = os.path.join(UPLOAD_FOLDER, "meetings")
os.makedirs(LOCAL_JSON_FOLDER, exist_ok=True)

# Load GCS bucket name (optional for local testing)
GCS_BUCKET = os.getenv("GCS_BUCKET")

# Init storage client if GCS is enabled
storage_client = storage.Client() if GCS_BUCKET else None

# Increase max request size to 100MB
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

@app.route('/')
def index():
    return "✅ RecMeet backend is running (local mode)."

@app.route('/upload', methods=['POST'])
def upload():
    start = time.time()
    try:
        file = request.files.get('audio')
        if not file:
            return jsonify({"error": "Missing 'audio' file in request"}), 400

        # Save audio as .m4a instead of .wav (requires ffmpeg for processing)
        unique_name = f"{uuid.uuid4()}.m4a"
        local_path = os.path.join(UPLOAD_FOLDER, unique_name)
        # Save audio file in chunks (streaming) to avoid memory overload
        with open(local_path, "wb") as f:
            while True:
                chunk = file.stream.read(8192)
                if not chunk:
                    break
                f.write(chunk)

        print(f"📥 Received file: {file.filename} → {local_path}")

        # Transcribe and summarize
        raw_transcript, detected_langs = transcribe_audio(local_path)
        cleaned_transcript, summary = summarize_transcript(raw_transcript, detected_langs)

        result = {
            "languages": detected_langs,
            "transcript": cleaned_transcript,
            "summary": summary
        }

        # Save result
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        json_filename = f"meeting_{timestamp}.json"

        if storage_client:
            blob_path = f"meetings/{json_filename}"
            bucket = storage_client.bucket(GCS_BUCKET)
            blob = bucket.blob(blob_path)
            blob.upload_from_string(
                data=json.dumps(result, indent=2, ensure_ascii=False),
                content_type='application/json'
            )
            print(f"✅ Uploaded to GCS: {blob_path}")
        else:
            local_json_path = os.path.join(LOCAL_JSON_FOLDER, json_filename)
            with open(local_json_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"✅ Saved locally: {local_json_path}")

        print(f"✅ Process completed in {time.time() - start:.2f} seconds")
        return jsonify(result)

    except Exception as e:
        print(f"❌ Error during upload processing: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/list', methods=['GET'])
def list_meetings():
    try:
        meetings = []

        if storage_client:
            blobs = storage_client.bucket(GCS_BUCKET).list_blobs(prefix="meetings/")
            for blob in blobs:
                if blob.name.endswith(".json") and "meeting_" in blob.name:
                    content = blob.download_as_text()
                    meetings.append(parse_meeting(blob.name, content))
        else:
            for filename in os.listdir(LOCAL_JSON_FOLDER):
                if filename.startswith("meeting_") and filename.endswith(".json"):
                    with open(os.path.join(LOCAL_JSON_FOLDER, filename), "r", encoding="utf-8") as f:
                        content = f.read()
                        meetings.append(parse_meeting(filename, content))

        meetings = [m for m in meetings if m]
        meetings.sort(key=lambda x: x["displayName"], reverse=True)
        return jsonify(meetings)

    except Exception as e:
        print(f"❌ Error during listing: {e}")
        return jsonify({"error": str(e)}), 500

def parse_meeting(filename, content):
    try:
        data = json.loads(content)
        transcript = data.get("transcript", "")
        summary = data.get("summary", "")
        timestamp_str = filename.replace("meeting_", "").replace(".json", "")
        dt = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S")
        display_name = dt.strftime("%m/%d/%Y (%H:%M:%S)") + " Meeting"
        return {
            "filename": filename,
            "displayName": display_name,
            "transcript": transcript,
            "summary": summary
        }
    except Exception as e:
        print(f"⚠️ Could not parse {filename}: {e}")
        return None

if __name__ == '__main__':
    pass  # Used by gunicorn