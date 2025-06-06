from faster_whisper import WhisperModel

model = WhisperModel("base", local_files_only=False)

def transcribe_audio(path):
    segments, info = model.transcribe(path, beam_size=5)
    text = " ".join([seg.text for seg in segments])
    langs = info.language
    return text, langs