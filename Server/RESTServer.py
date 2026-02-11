import datetime
from flask import Flask, redirect, request, jsonify
import time
import os
from io import BytesIO
from mutagen.wave import WAVE
import hashlib
import json
from functools import wraps
from threading import Lock
import mysql.connector
from datetime import datetime

app = Flask(__name__)

db_config = {
    'host': os.getenv('MYSQL_HOST'),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
    'database': os.getenv('MYSQL_DATABASE'),
    'port': 3306
}

BASE_DIR = "."
LOGS_DIR = "logs"
SAMPLES_DIR = "AudioSamples"
CONF_FILE_ALINODE = "alinode_conf.json"
CONF_DIR = "NodesConfiguration"
API_KEY=os.environ.get('API_KEY')
ADMIN_KEY=os.environ.get('ADMIN_KEY')
config_lock = Lock()
METADATA_KEYS = ["tmst", "noId", "blvl", "rmsv"]

def get_db_connection():
    try:
        conn = mysql.connector.connect(**db_config)
        return conn
    except mysql.connector.Error as err:
        print(f"Connection error: {err}")
        return None
    
TARGET_DOMAIN = "tesi.aliagrid.com"
REDIRECT_TARGET = f"https://{TARGET_DOMAIN}"

@app.before_request
def enforce_domain_and_https():
    # 1. Otteniamo l'host richiesto (es. "localhost:8443" o "123.45.67.89")
    requested_host = request.host.split(':')[0]  # Rimuove la porta se presente
    
    # 2. Controlliamo se la connessione è sicura (HTTPS)
    # Nota: Se sei dietro un proxy (Docker/Nginx), usa request.is_secure o 'X-Forwarded-Proto'
    is_https = request.is_secure or request.headers.get('X-Forwarded-Proto') == 'https'
    
    # 3. Logica di Redirect
    # Reindirizza se NON è HTTPS O se il dominio non coincide
    if not is_https or requested_host != TARGET_DOMAIN:
        # Costruiamo l'URL finale mantenendo il percorso (es. /configuration)
        new_url = f"https://{TARGET_DOMAIN}{request.full_path}"
        # Rimuove il punto interrogativo finale se non ci sono parametri
        new_url = new_url.rstrip('?')
        
        return redirect(new_url, code=301) # 301 = Permanent Redirect

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
                if key in METADATA_KEYS:
                    metadata[key] = value
                else:
                    raise Exception(f"Unexpected metadata key: {key}")
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

    # Legge i byte grezzi del body HTTP
    wav_bytes = request.get_data()
    audio_tmst=""
    
    
    try:

        if not wav_bytes:
            raise Exception("Empty request body")

        # Controlla magic bytes WAV
        if not is_valid_wav_bytes(wav_bytes):
            raise Exception("Chunk is not a valid WAV file")

         # Estrae metadati dal WAV (dai byte)
        metadata = getMetadata(wav_bytes)
        print("Received WAV metadata:", metadata)

        try:
            audio_tmst=datetime.strptime(metadata['tmst'], "%Y/%m/%d,%H:%M:%S")
        except Exception as e:
            raise Exception(f"Invalid timestamp format in metadata: {metadata.get('tmst', 'N/A')}. Expected format: YYYY/MM/DD,HH:MM:SS")
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 400
        
        
    try:

        # Salva il file WAV nella cartella AudioSamples
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
        samples_dir = os.path.join(base_dir, "AudioSamples")
        os.makedirs(samples_dir, exist_ok=True)

        # Salvataggio file
        hash_obj = hashlib.sha256(wav_bytes)
        hash_hex = hash_obj.hexdigest()
        filename = f"audio_{hash_hex}.wav"
        filepath = os.path.join(samples_dir, filename)

        try:

            with open(filepath, "wb") as f:
                f.write(wav_bytes)

            db=None
            cursor=None
            
            db = get_db_connection()
            if not db:
                raise Exception("Database connection failed")

            cursor = db.cursor()
        
            sql = """
                INSERT INTO AudioChunks (filename, timestamp, battery_level, node_id, rms)
                VALUES (%s, %s, %s, %s, %s)
                """
        
            values = (
                filename,           # Filename
                audio_tmst,   # Timestamp
                metadata['blvl'],   # Battery level
                metadata['noId'],   # Node ID
                metadata['rmsv']    # RMS value
            )

            try:
                cursor.execute(sql, values)
                db.commit()
                print(f"Insert executed successfully")
            except mysql.connector.Error as err:
                print(f"Error during insert: {err}")
                db.rollback()
                raise Exception(f"Database insert error: {err}")
            finally:
                if cursor:
                    cursor.close()
                if db:
                    db.close()
        except Exception as e:
            if os.path.exists(filepath):
                os.remove(filepath)  # Rimuove il file se c'è un errore nel DB
                print(f"Removed file {filename} due to error")
            raise e


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

    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type must be application/json"}), 415
    
    try:
        new_config = request.get_json()

        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), BASE_DIR))
        conf_dir = os.path.join(base_dir, CONF_DIR)
        conf_path = os.path.join(conf_dir, CONF_FILE_ALINODE)

        with config_lock:

            with open(conf_path, "w", encoding="utf-8") as f:
                json.dump(new_config, f, indent=4, ensure_ascii=False)

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
