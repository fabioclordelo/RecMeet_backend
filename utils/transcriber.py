from faster_whisper import WhisperModel
import torchaudio  # New import for audio loading and saving
import torch  # New import for tensor operations
import tempfile  # New import for temporary file creation
import os  # New import for file system operations

# Load model at module level (one-time load)
try:
    # Using "tiny" model as previously discussed for memory efficiency.
    # local_files_only is True because the "tiny" model is pre-bundled in the Dockerfile.
    model = WhisperModel("tiny", compute_type="int8", local_files_only=True)
except Exception as e:
    print(f"❌ Failed to load WhisperModel: {e}")
    model = None  # Fallback to prevent crashing at import time


def transcribe_audio(path, chunk_length=60):
    """
    Transcribes audio from a given path in chunks to manage memory usage.

    Args:
        path (str): The path to the audio file.
        chunk_length (int): The duration of each audio chunk in seconds.
                            Defaults to 60 seconds.

    Returns:
        tuple: A tuple containing the full transcribed text (str) and
               the detected language(s) (str).
    """
    if model is None:
        raise RuntimeError("Model is not loaded.")

    try:
        # Load the entire waveform once to get total duration and sample rate
        waveform, sample_rate = torchaudio.load(path)
        total_duration = waveform.size(1) / sample_rate  # Calculate total duration in seconds

        transcripts = []
        current_time = 0.0
        detected_language = None  # To store language from the first chunk

        print(f"Starting chunked transcription for total duration: {total_duration:.2f} seconds")

        while current_time < total_duration:
            # Determine the end time for the current chunk
            end_time = min(current_time + chunk_length, total_duration)

            # Extract the audio chunk
            # torchaudio.load returns (channels, samples), so waveform[:, start_sample:end_sample]
            start_sample = int(current_time * sample_rate)
            end_sample = int(end_time * sample_rate)
            chunk_waveform = waveform[:, start_sample:end_sample]

            # Create a temporary file to save the chunk
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio_file:
                temp_audio_path = temp_audio_file.name
                # Save the chunk to the temporary WAV file
                torchaudio.save(temp_audio_path, chunk_waveform, sample_rate)

            print(f"Transcribing chunk from {current_time:.2f}s to {end_time:.2f}s...")

            # Transcribe the current chunk
            segs, info = model.transcribe(temp_audio_path)

            # Collect the transcribed text for this chunk
            chunk_text = " ".join([seg.text for seg in segs])
            transcripts.append(chunk_text)

            # Store the language from the first chunk or update if needed
            if detected_language is None:
                detected_language = info.language

            # Clean up the temporary file
            os.unlink(temp_audio_path)

            # Move to the next chunk
            current_time = end_time

        full_transcript = " ".join(transcripts)
        print("Chunked transcription complete.")
        return full_transcript, detected_language

    except Exception as e:
        print(f"❌ Error during chunked transcription: {e}")
        raise RuntimeError(f"Failed to transcribe audio in chunks: {e}")

