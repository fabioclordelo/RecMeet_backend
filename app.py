from flask import Flask, request, jsonify
from utils.transcriber import transcribe_audio
from utils.summarizer import summarize_transcript
from google.cloud import storage, tasks_v2
import firebase_admin
from firebase_admin import credentials, messaging
import os
import json
import time
from datetime import datetime
import uuid

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Load environment variables
GCS_BUCKET = os.getenv("GCS_BUCKET")
GCP_PROJECT = os.getenv("GCP_PROJECT")
TASK_QUEUE = os.getenv("TASK_QUEUE")
TASK_LOCATION = os.getenv("TASK_LOCATION")
PROCESS_URL = os.getenv("PROCESS_URL")  # e.g., https://.../process
FIREBASE_CREDENTIAL_JSON = os.getenv("FIREBASE_CREDENTIAL_JSON")  # Full JSON string or file path

# Initialize GCS and FCM
storage_client = storage.Client()
DEVICE_TOKENS = set()

if not firebase_admin._apps:
    try:
        if os.path.isfile(FIREBASE_CREDENTIAL_JSON):
            cred = credentials.Certificate(FIREBASE_CREDENTIAL_JSON)
        else:
            cred = credentials.Certificate(json.loads(FIREBASE_CREDENTIAL_JSON))
        firebase_admin.initialize_app(cred)
        print("✅ Firebase initialized")
    except Exception as e:
        print("❌ Failed to initialize Firebase Admin SDK:", e)

@app.route('/')
def index():
    return "✅ RecMeet backend is running."

@app.route('/upload', methods=['POST'])
def upload():
    try:
        file = request.files.get('audio')
        if not file:
            return jsonify({"error": "Missing 'audio' file in request"}), 400

        unique_name = f"{uuid.uuid4()}.m4a"
        local_path = os.path.join(UPLOAD_FOLDER, unique_name)
        file.save(local_path)
        print(f"📥 Received file: {file.filename} → {local_path}")

        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(GCP_PROJECT, TASK_LOCATION, TASK_QUEUE)

        payload = {
            "local_path": local_path,
            "original_filename": file.filename
        }

        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": PROCESS_URL,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(payload).encode()
            }
        }

        response = client.create_task(parent=parent, task=task)
        print(f"🚀 Cloud Task enqueued: {response.name}")
        return jsonify({"status": "processing", "task": response.name}), 202

    except Exception as e:
        print(f"❌ Error during /upload: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/register_token', methods=['POST'])
def register_token():
    try:
        data = request.get_json()
        token = data.get("token")
        if token:
            DEVICE_TOKENS.add(token)
            print(f"✅ Registered FCM token: {token}")
            return jsonify({"status": "registered"}), 200
        return jsonify({"error": "No token provided"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def notify_clients(filename):
    for token in DEVICE_TOKENS:
        try:
            message = messaging.Message(
                data={"filename": filename},
                token=token
            )
            response = messaging.send(message)
            print(f"🔔 FCM sent to {token}: {response}")
        except Exception as e:
            print(f"⚠️ Failed to send FCM to {token}: {e}")

@app.route('/process', methods=['POST'])
def process():
    try:
        data = request.get_json()
        local_path = data.get("local_path")
        original_filename = data.get("original_filename", "uploaded.m4a")
        print(f"⚙️ Processing file: {original_filename} at {local_path}")

        start = time.time()

        raw_transcript, detected_langs = transcribe_audio(local_path)
        cleaned_transcript, summary = summarize_transcript(raw_transcript, detected_langs)

        result = {
            "languages": detected_langs,
            "transcript": cleaned_transcript,
            "summary": summary
        }

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
        notify_clients(json_filename)
        print(f"✅ Task processed in {time.time() - start:.2f} seconds")

        return jsonify({"status": "done", "filename": json_filename}), 200

    except Exception as e:
        print(f"❌ Error during /process: {e}")
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
                        print(f"⚠️ Could not parse timestamp in {filename}: {e}")

        meetings.sort(key=lambda x: x["displayName"], reverse=True)
        return jsonify(meetings)

    except Exception as e:
        print(f"❌ Error during listing: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/status/<filename>', methods=['GET'])
def get_meeting(filename):
    try:
        bucket = storage_client.bucket(GCS_BUCKET)
        blob = bucket.blob(f"meetings/{filename}")
        if not blob.exists():
            return jsonify({"error": "File not found"}), 404

        content = blob.download_as_text()
        data = json.loads(content)

        timestamp_str = filename[len("meeting_"):-len(".json")]
        dt = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S")
        display_name = dt.strftime("%m/%d/%Y (%H:%M:%S)") + " Meeting"

        return jsonify({
            "filename": filename,
            "displayName": display_name,
            "transcript": data.get("transcript", ""),
            "summary": data.get("summary", "")
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    pass  # Used by gunicorn
