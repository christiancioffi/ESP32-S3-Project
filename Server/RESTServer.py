from flask import Flask, request, jsonify
import base64
import json
import time

app = Flask(__name__)

@app.route('/audio', methods=['POST'])
def audio():
    try:
        chunk = json.loads(request.get_json())
        if 'data' not in chunk:
            return jsonify({"status": "error", "message": "Missing 'data' field"}), 400

        wav_data_base64 = chunk["data"]
        wav_data_bytes = base64.b64decode(wav_data_base64)

        timestamp = time.time() #Prendere dal chunk ricevuto (chunk[timestamp])
        filename = "sample_"+str(timestamp)+".wav"
        with open(filename, "wb") as f:
            f.write(wav_data_bytes)

        return jsonify({"status": "ok", "file": filename})

    except Exception as e:
        print("caught exception {} {}".format(type(e).__name__, e))
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=True)
