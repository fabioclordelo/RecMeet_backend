from flask import Flask, request, jsonify
from utils.transcriber import transcribe_audio
from utils.summarizer import summarize_transcript
from google.cloud import storage, tasks_v2
import firebase_admin
from firebase_admin import credentials, messaging, firestore
import os
import json
import time
from datetime import datetime, timedelta
import uuid
import tempfile
from google.cloud.storage import Blob

# New imports for explicit credential handling
import google.auth
import google.auth.transport.requests

app = Flask(__name__)
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


@app.route('/upload', methods=['POST'])
def upload():
    """
    This endpoint now expects a JSON payload with 'gcs_blob_name' and 'original_filename'
    after the client has directly uploaded the file to GCS using a signed URL.
    It then enqueues a Cloud Task for processing.
    """
    try:
        data = request.get_json()
        gcs_blob_name = data.get("gcs_blob_name")
        original_filename = data.get("original_filename", "uploaded_file.m4a")

        if not gcs_blob_name:
            return jsonify({"error": "Missing 'gcs_blob_name' in request payload"}), 400

        print(f"📥 Received notification for GCS blob: gs://{GCS_BUCKET}/{gcs_blob_name}")

        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(GCP_PROJECT, TASK_LOCATION, TASK_QUEUE)

        payload = {
            "gcs_blob_name": gcs_blob_name,
            "original_filename": original_filename
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
        print(f"🚀 Cloud Task enqueued for GCS blob: {response.name}")
        return jsonify({"status": "processing", "task": response.name}), 202

    except Exception as e:
        print(f"❌ Error during /upload (after GCS direct upload): {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/get_signed_upload_url', methods=['POST'])
def get_signed_upload_url():
    """
    Generates a signed URL for direct client-to-GCS upload.
    The client should provide the desired filename for the GCS object.
    """
    try:
        data = request.get_json()
        client_filename = data.get("filename")
        if not client_filename:
            return jsonify({"error": "Missing 'filename' in request payload"}), 400

        # Generate a unique blob name for the raw audio in GCS
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        # Ensure the filename is safe for GCS paths (e.g., replace spaces)
        safe_client_filename = client_filename.replace(" ", "_")
        gcs_blob_name = f"raw_audio/{timestamp}_{uuid.uuid4().hex}_{safe_client_filename}"

        bucket = storage_client.bucket(GCS_BUCKET)
        blob = bucket.blob(gcs_blob_name)

        # Explicitly get credentials and service account email for signing
        # This is the key change to address the "private key to sign credentials" error
        credentials, project = google.auth.default()
        request_transport = google.auth.transport.requests.Request()
        credentials.refresh(request_transport)  # Ensure credentials are fresh and contain access token

        # Generate the signed URL for uploading
        # The URL will be valid for 15 minutes (900 seconds)
        signed_url = blob.generate_signed_url(
            version="v4",
            expiration=datetime.now() + timedelta(minutes=15),  # URL valid for 15 minutes
            method="PUT",  # Method for uploading a file
            content_type="audio/m4a",  # Specify content type for the upload
            service_account_email=credentials.service_account_email,  # Explicitly pass SA email
            access_token=credentials.token  # Explicitly pass access token
        )

        print(f"✅ Generated signed URL for GCS blob: {gcs_blob_name}")
        return jsonify({
            "signed_url": signed_url,
            "gcs_blob_name": gcs_blob_name,
            "original_filename": client_filename  # Return original filename for context
        }), 200

    except Exception as e:
        print(f"❌ Error generating signed URL: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/process', methods=['POST'])
def process():
    """
    Processes an audio file: downloads from GCS, transcribes, summarizes,
    uploads results to GCS, and notifies clients via FCM.
    """
    gcs_blob_name = None
    local_audio_path = None

    try:
        data = request.get_json()
        gcs_blob_name = data.get("gcs_blob_name")
        original_filename = data.get("original_filename", "uploaded.m4a")

        if not gcs_blob_name:
            return jsonify({"error": "Missing 'gcs_blob_name' in request payload"}), 400

        print(f"⚙️ Processing GCS blob: gs://{GCS_BUCKET}/{gcs_blob_name}")

        start = time.time()

        # Download audio from GCS to a temporary local file
        bucket = storage_client.bucket(GCS_BUCKET)
        blob = bucket.blob(gcs_blob_name)

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
        print(f"❌ Error during /process: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
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
