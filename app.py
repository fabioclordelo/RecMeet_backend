from flask import Flask, request, jsonify
from utils.transcriber import transcribe_audio
from utils.summarizer import summarize_transcript
from google.cloud import storage, tasks_v2
import firebase_admin
from firebase_admin import credentials, messaging, firestore
import os
import json
import time
from datetime import datetime
import uuid
import tempfile  # New import for temporary files

app = Flask(__name__)
# UPLOAD_FOLDER is no longer strictly for *saving* uploads, but for temporary storage during processing
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Load environment variables
GCS_BUCKET = os.getenv("GCS_BUCKET")
GCP_PROJECT = os.getenv("GCP_PROJECT")
TASK_QUEUE = os.getenv("TASK_QUEUE")
TASK_LOCATION = os.getenv("TASK_LOCATION")
PROCESS_URL = os.getenv("PROCESS_URL")
FIREBASE_CREDENTIAL_JSON = os.getenv("FIREBASE_CREDENTIAL_JSON")
TOKENS_COLLECTION = "fcm_tokens"

# Initialize Firebase Admin SDK
if not firebase_admin._apps:
    try:
        if os.path.isfile(FIREBASE_CREDENTIAL_JSON):
            cred = credentials.Certificate(FIREBASE_CREDENTIAL_JSON)
        else:
            cred = credentials.Certificate(json.loads(FIREBASE_CREDENTIAL_JSON))
        firebase_admin.initialize_app(cred)
        print("✅ Firebase initialized")
    except Exception as e:
        print(f"❌ Failed to initialize Firebase Admin SDK: {e}")

# ✅ Now safe to use Firestore client
firestore_client = firestore.client()
storage_client = storage.Client()


@app.route('/')
def index():
    """Root endpoint to confirm backend is running."""
    return "✅ RecMeet backend is running."


@app.route('/upload', methods=['POST'])
def upload():
    """
    Handles audio file uploads. Instead of saving locally, it uploads directly to GCS
    and then enqueues a Cloud Task with the GCS blob name.
    """
    try:
        file = request.files.get('audio')
        if not file:
            return jsonify({"error": "Missing 'audio' file in request"}), 400

        # Generate a unique name for the audio file in GCS
        # Using a timestamp and UUID for uniqueness
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        gcs_blob_name = f"raw_audio/{timestamp}_{uuid.uuid4().hex}.m4a"

        # Upload the received audio file directly to GCS
        bucket = storage_client.bucket(GCS_BUCKET)
        blob = bucket.blob(gcs_blob_name)
        blob.upload_from_file(file)  # Uploads file-like object directly
        print(f"📥 Uploaded audio to GCS: gs://{GCS_BUCKET}/{gcs_blob_name}")

        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(GCP_PROJECT, TASK_LOCATION, TASK_QUEUE)

        payload = {
            "gcs_blob_name": gcs_blob_name,  # Pass GCS blob name, not local path
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
    """
    Registers a Firebase Cloud Messaging (FCM) token in Firestore.
    This token is used to send push notifications to the client app.
    """
    try:
        data = request.get_json()
        token = data.get("token")
        if not token:
            return jsonify({"error": "No token provided"}), 400

        doc_ref = firestore_client.collection(TOKENS_COLLECTION).document(token)
        doc_ref.set({"timestamp": firestore.SERVER_TIMESTAMP})
        print(f"✅ Registered FCM token in Firestore: {token}")

        return jsonify({"status": "registered"}), 200

    except Exception as e:
        print(f"❌ Error registering FCM token: {e}")
        return jsonify({"error": str(e)}), 500


def notify_clients(filename):
    """
    Sends FCM notifications to all registered client tokens.
    Includes 'content_available=True' for better background delivery on Apple platforms.
    """
    try:
        tokens_ref = firestore_client.collection(TOKENS_COLLECTION)
        docs = tokens_ref.stream()
        tokens = [doc.id for doc in docs]

        if not tokens:
            print("⚠️ No FCM tokens in Firestore. Skipping notifications.")
            return

        for token in tokens:
            try:
                message = messaging.Message(
                    notification=messaging.Notification(
                        title="RecMeet Update",
                        body=f"Your transcript is ready! {uuid.uuid4().hex[:6]}"
                    ),
                    data={"filename": filename},
                    token=token,
                    apns=messaging.APNSConfig(
                        headers={"apns-priority": "10"},
                        payload=messaging.APNSPayload(
                            aps=messaging.Aps(content_available=True)
                        )
                    )
                )
                print(f"Attempting to send FCM to token: {token} for filename: {filename}")
                response = messaging.send(message)
                print(f"🔔 FCM sent successfully to {token}. Response: {response}")
            except messaging.UnregisteredError:
                print(f"❌ FCM token {token} is unregistered or invalid. Removing from Firestore.")
                firestore_client.collection(TOKENS_COLLECTION).document(token).delete()
            except Exception as e:
                print(f"❌ Failed to send FCM to {token}. Error: {e}", exc_info=True)

    except Exception as e:
        print(f"❌ Failed to notify clients (outer loop): {e}", exc_info=True)


@app.route('/process', methods=['POST'])
def process():
    """
    Processes an audio file: downloads from GCS, transcribes, summarizes,
    uploads results to GCS, and notifies clients via FCM.
    """
    gcs_blob_name = None  # Initialize to None for error handling
    local_audio_path = None  # Initialize to None for cleanup

    try:
        data = request.get_json()
        gcs_blob_name = data.get("gcs_blob_name")  # Get GCS blob name from payload
        original_filename = data.get("original_filename", "uploaded.m4a")

        if not gcs_blob_name:
            return jsonify({"error": "Missing 'gcs_blob_name' in request payload"}), 400

        print(f"⚙️ Processing GCS blob: gs://{GCS_BUCKET}/{gcs_blob_name}")

        start = time.time()

        # Download audio from GCS to a temporary local file
        bucket = storage_client.bucket(GCS_BUCKET)
        blob = bucket.blob(gcs_blob_name)

        # Create a temporary file to save the downloaded audio
        with tempfile.NamedTemporaryFile(delete=False, suffix=".m4a") as temp_file:
            local_audio_path = temp_file.name
            blob.download_to_filename(local_audio_path)
        print(f"⬇️ Downloaded audio from GCS to local path: {local_audio_path}")

        # Transcribe and summarize
        raw_transcript, detected_langs = transcribe_audio(local_audio_path)
        cleaned_transcript, summary = summarize_transcript(raw_transcript, detected_langs)

        result = {
            "languages": detected_langs,
            "transcript": cleaned_transcript,
            "summary": summary
        }

        # Generate a unique filename for the JSON output in GCS
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        json_filename = f"meeting_{timestamp}.json"
        blob_path = f"meetings/{json_filename}"

        # Upload the JSON result to Google Cloud Storage
        result_blob = bucket.blob(blob_path)
        result_blob.upload_from_string(
            data=json.dumps(result, indent=2, ensure_ascii=False),
            content_type='application/json'
        )

        print(f"✅ Uploaded result to GCS: {blob_path}")

        # Optional: Verify blob exists (can sometimes be eventually consistent)
        for attempt in range(5):
            if result_blob.exists():
                print("🟢 Verified result blob exists in GCS.")
                break
            print(f"⏳ Attempt {attempt + 1}/5: Waiting for result blob to be available...")
            time.sleep(0.5)
        else:
            print("⚠️ Result blob not confirmed in time — continuing anyway.")

        # Notify clients that processing is complete
        print("Attempting to notify clients...")
        notify_clients(json_filename)
        print("Client notification attempt completed.")

        print(f"✅ Task processed in {time.time() - start:.2f} seconds")

        return jsonify({"status": "done", "filename": json_filename}), 200

    except Exception as e:
        print(f"❌ Error during /process: {e}", exc_info=True)  # Print full traceback
        return jsonify({"error": str(e)}), 500
    finally:
        # Clean up the temporary local audio file
        if local_audio_path and os.path.exists(local_audio_path):
            try:
                os.unlink(local_audio_path)
                print(f"🗑️ Cleaned up temporary file: {local_audio_path}")
            except Exception as e:
                print(f"⚠️ Failed to delete temporary file {local_audio_path}: {e}")


@app.route('/list', methods=['GET'])
def list_meetings():
    """
    Lists all processed meeting JSON files stored in GCS.
    """
    try:
        bucket = storage_client.bucket(GCS_BUCKET)
        # List blobs in the 'meetings/' prefix
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
    """
    Retrieves a specific meeting JSON file from GCS by filename.
    """
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
        print(f"❌ Error getting meeting {filename}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/debug_notify', methods=['POST'])
def debug_notify():
    """
    Endpoint to manually send a debug notification to a specific FCM token.
    Useful for testing FCM delivery without triggering a full audio processing.
    """
    try:
        data = request.get_json()
        token = data.get("token")
        if not token:
            return jsonify({"error": "Missing token"}), 400

        message = messaging.Message(
            notification=messaging.Notification(
                title="Debug Notification",
                body="This is a test push from RecMeet backend."
            ),
            data={"filename": "debug.json"},
            token=token,
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10"},
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(content_available=True)
                )
            )
        )
        response = messaging.send(message)
        print(f"🔔 Debug notification sent to {token}: {response}")
        return jsonify({"status": "sent", "response": str(response)}), 200

    except Exception as e:
        print(f"❌ Error sending debug FCM: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/test_manual_notify', methods=['POST'])
def test_manual_notify():
    """
    New endpoint to manually trigger a notification to a specific device token
    with a custom filename.
    """
    try:
        data = request.get_json()
        token = data.get("token")
        test_filename = data.get("filename", "manual_test.json")

        if not token:
            return jsonify({"error": "No token provided"}), 400

        message = messaging.Message(
            notification=messaging.Notification(
                title="Manual Test Notification",
                body=f"This is a manual test for {test_filename}."
            ),
            data={"filename": test_filename},
            token=token,
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10"},
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(content_available=True)
                )
            )
        )
        print(f"Attempting to send manual FCM to token: {token} for filename: {test_filename}")
        response = messaging.send(message)
        print(f"🔔 Manual debug notification sent to {token}. Response: {response}")
        return jsonify({"status": "sent", "response": str(response)}), 200

    except Exception as e:
        print(f"❌ Error sending manual debug FCM: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    pass
