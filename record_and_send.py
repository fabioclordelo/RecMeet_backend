import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
import requests

SAMPLE_RATE = 44100
DURATION = 10  # seconds
CHANNELS = 3
FILENAME = "meeting_audio.wav"
DEVICE_INDEX = 8  # << Replace with the actual device index from query_devices

print("Recording... Speak now or play audio.")
recording = sd.rec(
    int(DURATION * SAMPLE_RATE),
    samplerate=SAMPLE_RATE,
    channels=CHANNELS,
    device=DEVICE_INDEX,
    dtype='int16'
)

sd.wait()
wav.write(FILENAME, SAMPLE_RATE, recording)
print("Recording complete, sending...")

with open(FILENAME, "rb") as f:
    res = requests.post("http://localhost:5000/upload", files={"audio": f})

print("Status:", res.status_code)
print("Response:")
print(res.text)
