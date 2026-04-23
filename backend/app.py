from __future__ import annotations

import traceback

from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

from summarizer import summarize_text

load_dotenv()

app = Flask(__name__)
CORS(app)


@app.get("/health")
def health_check():
    return jsonify({"ok": True})


@app.post("/summarize")
def summarize():
    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "")
    length = payload.get("length", "medium")

    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Field 'text' must be a non-empty string."}), 400
    if length not in {"short", "medium", "long"}:
        return jsonify({"error": "Field 'length' must be short, medium, or long."}), 400

    try:
        result = summarize_text(text, length)
        return jsonify(result), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"Summarization failed: {exc}"}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
