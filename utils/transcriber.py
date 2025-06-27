from faster_whisper import WhisperModel

# Load model at module level (one-time load)
try:
    model = WhisperModel("tiny", compute_type="int8", local_files_only=True)
except Exception as e:
    print("Failed to load WhisperModel:", e)
    model = None  # Fallback to prevent crashing at import time

def transcribe_audio(path):
    if model is None:
        raise RuntimeError("Model is not loaded.")
    segments, info = model.transcribe(path)  # greedy decoding = faster
    text = " ".join([seg.text for seg in segments])
    langs = info.language
    return text, langs