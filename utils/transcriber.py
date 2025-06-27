from faster_whisper import WhisperModel

# Load model at module level (one-time load)
try:
    model = WhisperModel("base", compute_type="int8", local_files_only=True)
except Exception as e:
    print("Failed to load WhisperModel:", e)
    model = None  # Fallback to prevent crashing at import time

def transcribe_audio(path, chunk_length=60):
    """
    Transcribes audio in smaller chunks to reduce memory usage.
    Returns the full transcript and detected language.
    """
    if model is None:
        raise RuntimeError("Model is not loaded.")

    import torchaudio
    import tempfile
    import os

    waveform, sample_rate = torchaudio.load(path)
    total_duration = waveform.size(1) / sample_rate

    transcripts = []
    current = 0
    lang = "en"

    while current < total_duration:
        end = min(current + chunk_length, total_duration)
        chunk_waveform = waveform[:, int(current * sample_rate):int(end * sample_rate)]

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
            torchaudio.save(temp_audio.name, chunk_waveform, sample_rate)
            segs, info = model.transcribe(temp_audio.name)
            transcripts.append(" ".join([seg.text for seg in segs]))
            os.unlink(temp_audio.name)

            # Detect language from the first chunk only
            if current == 0:
                lang = info.language

        current += chunk_length

    return " ".join(transcripts), lang
