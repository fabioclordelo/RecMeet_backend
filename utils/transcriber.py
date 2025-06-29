from faster_whisper import WhisperModel
import subprocess  # New import for running ffmpeg
import os  # For file system operations (temp files)
import tempfile  # For creating temporary files
import math  # For calculating chunk durations

# Load model at module level (one-time load)
try:
    # Using "tiny" model for memory efficiency.
    # local_files_only is True because the "tiny" model is pre-bundled in the Dockerfile.
    model = WhisperModel("tiny", compute_type="int8", local_files_only=True)
except Exception as e:
    print(f"❌ Failed to load WhisperModel: {e}")
    model = None  # Fallback to prevent crashing at import time


def get_audio_duration(file_path):
    """
    Gets the duration of an audio file using ffprobe (part of ffmpeg).
    """
    try:
        command = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"❌ Error getting audio duration for {file_path}: {e}")
        raise RuntimeError(f"Failed to get audio duration: {e}")


def transcribe_audio(full_audio_path, chunk_length_seconds=240):
    """
    Transcribes audio from a given path in chunks using ffmpeg to extract segments.
    This avoids loading the entire audio file into Python memory at once.

    Args:
        full_audio_path (str): The path to the complete audio file on local disk.
        chunk_length_seconds (int): The duration of each audio chunk in seconds.
                                    Defaults to 30 seconds.

    Returns:
        tuple: A tuple containing the full transcribed text (str) and
               the detected language(s) (str).
    """
    if model is None:
        raise RuntimeError("Model is not loaded.")

    try:
        total_duration = get_audio_duration(full_audio_path)
        print(f"⚙️ Starting chunked transcription for total duration: {total_duration:.2f} seconds")

        transcripts = []
        detected_language = None

        num_chunks = math.ceil(total_duration / chunk_length_seconds)

        for i in range(num_chunks):
            start_time = i * chunk_length_seconds
            # Ensure end_time doesn't exceed total_duration
            end_time = min((i + 1) * chunk_length_seconds, total_duration)

            # Create a temporary file for the current audio chunk
            with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as temp_chunk_file:
                temp_chunk_path = temp_chunk_file.name

            # Use ffmpeg to extract the chunk
            # -ss: start time
            # -t: duration
            # -i: input file
            # -acodec copy: copy audio codec (no re-encoding, faster)
            ffmpeg_command = [
                "ffmpeg",
                "-ss", str(start_time),
                "-i", full_audio_path,
                "-t", str(end_time - start_time),  # Duration of the chunk
                "-c:a", "copy",  # Copy audio stream without re-encoding
                "-map_metadata", "-1",  # Remove metadata
                "-y",  # Overwrite output files without asking
                temp_chunk_path
            ]

            print(f"Extracting chunk {i + 1}/{num_chunks} ({start_time:.2f}s to {end_time:.2f}s)...")
            subprocess.run(ffmpeg_command, check=True, capture_output=True)

            # Transcribe the current chunk
            print(f"Transcribing chunk {i + 1}/{num_chunks}...")
            segs, info = model.transcribe(temp_chunk_path)

            # Collect the transcribed text for this chunk
            chunk_text = " ".join([seg.text for seg in segs])
            transcripts.append(chunk_text)

            # Store the language from the first chunk
            if detected_language is None:
                detected_language = info.language

            # Clean up the temporary chunk file
            os.unlink(temp_chunk_path)
            print(f"🗑️ Cleaned up temporary chunk file: {temp_chunk_path}")

        full_transcript = " ".join(transcripts)
        print("✅ Chunked transcription complete.")
        return full_transcript, detected_language

    except subprocess.CalledProcessError as e:
        print(f"❌ FFmpeg command failed: {e.cmd}")
        print(f"STDOUT: {e.stdout.decode()}")
        print(f"STDERR: {e.stderr.decode()}")
        raise RuntimeError(f"FFmpeg chunk extraction failed: {e}")
    except Exception as e:
        print(f"❌ Error during chunked transcription: {e}")
        raise RuntimeError(f"Failed to transcribe audio in chunks: {e}")

