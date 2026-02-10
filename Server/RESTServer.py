from flask import Flask, request, jsonify
import time
import os
from io import BytesIO
from mutagen.wave import WAVE
import hashlib
import json
from functools import wraps
from threading import Lock

app = Flask(__name__)

BASE_DIR = "."
LOGS_DIR = "logs"
SAMPLES_DIR = "AudioSamples"
CONF_FILE_ALINODE = "alinode_conf.json"
CONF_DIR = "NodesConfiguration"
API_KEY=os.environ.get('API_KEY')
ADMIN_KEY=os.environ.get('ADMIN_KEY')
config_lock = Lock()

def api_key_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-KEY')
        
        if api_key and api_key == API_KEY:
            return f(*args, **kwargs)
        else:
            return jsonify({"error": "Missing (or invalid) API KEY"}), 401
            
    return decorated_function

def admin_key_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_key = request.headers.get('X-ADMIN-KEY')
        
        if admin_key and admin_key == ADMIN_KEY:
            return f(*args, **kwargs)
        else:
            return jsonify({"error": "Missing (or invalid) ADMIN KEY"}), 401
            
    return decorated_function

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
@api_key_required
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

        # Salva il file WAV nella cartella AudioSamples
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
        samples_dir = os.path.join(base_dir, "AudioSamples")
        os.makedirs(samples_dir, exist_ok=True)

        # Salvataggio file
        hash_obj = hashlib.sha256(wav_bytes)
        hash_hex = hash_obj.hexdigest()
        filename = f"audio_{hash_hex}.wav"
        filepath = os.path.join(samples_dir, filename)

        with open(filepath, "wb") as f:
            f.write(wav_bytes)

        return jsonify({
            "status": "Chunk received successfully",
        })

    except Exception as e:
        print(f"caught exception {type(e).__name__} {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/logs', methods=['POST'])
@api_key_required
def save_logs():
    try:
        data = request.get_data().decode("utf-8")

        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), BASE_DIR))
        logs_dir = os.path.join(base_dir, LOGS_DIR)
        timestamp = str(time.time()).replace(".", "_")
        filename=f"log_{timestamp}.txt"

        with open(os.path.join(logs_dir, filename), "w", encoding="utf-8") as f:
                f.write(data)

        return jsonify({
                "status": "Logs saved successfully",
        })
        
    except Exception as e:
        print(f"caught exception {type(e).__name__} {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/configuration', methods=['GET'])
@api_key_required
def get_configuration():
    try:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), BASE_DIR))
        conf_dir = os.path.join(base_dir, CONF_DIR)
        conf_path = os.path.join(conf_dir, CONF_FILE_ALINODE)
        config_data = ""

        with config_lock:

            with open(conf_path, "r", encoding="utf-8") as f:
                config_data =  json.load(f)

        return jsonify(config_data)
        
    except Exception as e:
        print(f"caught exception {type(e).__name__} {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/configuration', methods=['POST'])
@admin_key_required
def update_configuration():
    try:
        data = request.get_data().decode("utf-8")

        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), BASE_DIR))
        conf_dir = os.path.join(base_dir, CONF_DIR)
        conf_path = os.path.join(conf_dir, CONF_FILE_ALINODE)

        try:
            json.loads(data)
        except ValueError:
            return jsonify({"status": "error", "message": "JSON not valid"}), 400

        with config_lock:

            with open(conf_path, "w", encoding="utf-8") as f:
                f.write(data)

        return jsonify({
                "status": "Configuration updated successfully",
        })
        
    except Exception as e:
        print(f"caught exception {type(e).__name__} {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


if __name__ == '__main__':
    os.makedirs(LOGS_DIR, exist_ok=True)
    app.run(host='0.0.0.0', port=8443, debug=True)
