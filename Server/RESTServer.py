from flask import Flask, request, jsonify
import time
import os
from time import time
from io import BytesIO
from mutagen.wave import WAVE

app = Flask(__name__)

def is_valid_wav_bytes(data):
    return (
        len(data) >= 44 and
        data[0:4] == b"RIFF" and
        data[8:12] == b"WAVE"
    )

def getMetadata(wav_bytes):
    metadata = {}
    i = 12  # salta RIFF + size + WAVE

    size = len(wav_bytes)

    while i + 8 <= size:
        chunk_id = wav_bytes[i:i+4]
        chunk_size = int.from_bytes(wav_bytes[i+4:i+8], "little")
        chunk_data_start = i + 8
        chunk_data_end = chunk_data_start + chunk_size

        if chunk_id == b"LIST" and wav_bytes[chunk_data_start:chunk_data_start+4] == b"INFO":
            j = chunk_data_start + 4  # salta "INFO"

            while j + 8 <= chunk_data_end:
                key = wav_bytes[j:j+4].decode("ascii", errors="ignore").strip()
                length = int.from_bytes(wav_bytes[j+4:j+8], "little")
                value_bytes = wav_bytes[j+8:j+8+length]

                # rimuove \x00 finali
                value = value_bytes.rstrip(b"\x00").decode("ascii", errors="ignore")

                metadata[key] = value
                j += 8 + length

        # chunk allineati a 2 byte
        i = chunk_data_end + (chunk_size % 2)

    return metadata

@app.route('/', methods=['GET'])
def home():
    return "Audio REST server is running."

@app.route('/audio', methods=['POST'])
def audio():
    try:
        # Legge i byte grezzi del body HTTP
        wav_bytes = request.get_data()

        if not wav_bytes:
            return jsonify({
                "status": "error",
                "message": "Empty request body"
            }), 400

        # Controlla magic bytes WAV
        if not is_valid_wav_bytes(wav_bytes):
            return jsonify({
                "status": "error",
                "message": "Body is not a valid WAV file"
            }), 400

        # Estrae metadati dal WAV (dai byte)
        metadata = getMetadata(wav_bytes)
        print("Received WAV metadata:", metadata)

        # Salva il file WAV nella cartella Samples
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        samples_dir = os.path.join(base_dir, "Samples")
        os.makedirs(samples_dir, exist_ok=True)

        # Salvataggio file
        timestamp = metadata["tmst"]
        filename = f"sample_{timestamp}.wav"
        filepath = os.path.join(samples_dir, filename)

        with open(filepath, "wb") as f:
            f.write(wav_bytes)

        return jsonify({
            "status": "ok",
        })

    except Exception as e:
        print(f"caught exception {type(e).__name__} {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8443, debug=True)
