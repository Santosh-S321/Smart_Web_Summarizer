from __future__ import annotations

import os
import re
import time
from functools import lru_cache
from json import JSONDecodeError
from typing import Dict, List

import requests
from transformers import pipeline

# ---- Limits ----
MAX_INPUT_CHARS = 20000
CHUNK_CHAR_SIZE = 2200
CHUNK_OVERLAP = 200
CHUNK_MAX_LENGTH = 120
CHUNK_MIN_LENGTH = 45
MAX_BULLETS = 5

# ---- Models ----
DEFAULT_LOCAL_MODEL = "sshleifer/distilbart-cnn-12-6"
DEFAULT_HF_API_MODEL = "facebook/bart-large-cnn"

# ---- Hosted API settings ----
HF_API_TIMEOUT_SECONDS = int(os.getenv("HF_API_TIMEOUT_SECONDS", "90"))
HF_API_RETRIES_PER_ENDPOINT = int(os.getenv("HF_API_RETRIES_PER_ENDPOINT", "2"))
DEFAULT_HF_INFERENCE_URLS = [
    "https://router.huggingface.co/hf-inference/models/{model}",
]

LENGTH_PRESETS = {
    "short": {"max_length": 80, "min_length": 30},
    "medium": {"max_length": 140, "min_length": 50},
    "long": {"max_length": 220, "min_length": 80},
}


# ---------- Text helpers ----------

def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def split_into_chunks(text: str, chunk_size: int = CHUNK_CHAR_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    if len(text) <= chunk_size:
        return [text]

    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = max(end - overlap, 0)
    return chunks


def extract_bullets(summary: str, limit: int = MAX_BULLETS) -> List[str]:
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    return [s.strip() for s in sentences if s.strip()][:limit]


# ---------- Configuration ----------

def should_use_remote_api() -> bool:
    return os.getenv("USE_HF_INFERENCE_API", "false").lower() == "true"


def get_local_model_name() -> str:
    return os.getenv("LOCAL_MODEL", DEFAULT_LOCAL_MODEL)


def get_hf_api_model_name() -> str:
    return os.getenv("HF_API_MODEL", DEFAULT_HF_API_MODEL)


def get_hf_inference_urls() -> List[str]:
    raw = os.getenv("HF_INFERENCE_URLS", "").strip()
    if not raw:
        return DEFAULT_HF_INFERENCE_URLS
    urls = [u.strip() for u in raw.split(",") if u.strip()]
    return urls or DEFAULT_HF_INFERENCE_URLS


# ---------- Local inference ----------

@lru_cache(maxsize=1)
def get_summarizer():
    return pipeline("summarization", model=get_local_model_name())


def summarize_locally(text: str, max_length: int, min_length: int) -> str:
    result = get_summarizer()(
        text,
        max_length=max_length,
        min_length=min_length,
        do_sample=False,
        truncation=True,  # prevents crashes when input exceeds the model's token limit
    )
    return result[0]["summary_text"]


# ---------- Hosted inference ----------

def summarize_with_hf_api(text: str, max_length: int, min_length: int) -> str:
    token = os.getenv("HF_API_TOKEN", "").strip()
    if not token:
        raise ValueError("HF_API_TOKEN is required when USE_HF_INFERENCE_API=true")

    model = get_hf_api_model_name()
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "inputs": text,
        "parameters": {
            "max_length": max_length,
            "min_length": min_length,
            "do_sample": False,
        },
        "options": {"wait_for_model": True},
    }

    errors: List[str] = []
    for url_template in get_hf_inference_urls():
        url = url_template.format(model=model)
        for attempt in range(1, HF_API_RETRIES_PER_ENDPOINT + 1):
            attempt_label = f"attempt {attempt}/{HF_API_RETRIES_PER_ENDPOINT}"
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=HF_API_TIMEOUT_SECONDS)
            except requests.Timeout as exc:
                errors.append(f"Timeout from {url} ({attempt_label}): {exc}")
                continue
            except requests.RequestException as exc:
                errors.append(f"Request failure from {url} ({attempt_label}): {exc}")
                continue

            if response.status_code == 404:
                errors.append(f"404 from {url}: {response.text[:200]}")
                break
            if response.status_code >= 500:
                errors.append(f"{response.status_code} from {url}: {response.text[:200]}")
                continue
            if response.status_code >= 400:
                raise ValueError(f"Hugging Face API error: {response.status_code} {response.text[:300]}")

            try:
                data = response.json()
            except JSONDecodeError:
                errors.append(f"Invalid JSON from {url}: {response.text[:200]}")
                continue

            if not isinstance(data, list) or not data or "summary_text" not in data[0]:
                errors.append(f"Unexpected response format from {url}: {str(data)[:200]}")
                continue
            return data[0]["summary_text"]

    last_error = errors[-1] if errors else "Unknown Hugging Face API error"
    raise ValueError(f"Hugging Face API request failed for model '{model}'. Last error: {last_error}")


def summarize_with_hf_api_safe(text: str, max_length: int, min_length: int) -> str:
    # Remote models can reject overlong inputs with "index out of range in self".
    # Retry by trimming input and reducing generation length.
    attempts = [
        (1.00, max_length, min_length),
        (0.80, min(max_length, 120), min(min_length, 45)),
        (0.60, min(max_length, 100), min(min_length, 35)),
        (0.45, min(max_length, 80), min(min_length, 25)),
    ]

    last_error = "Unknown error"
    for ratio, try_max, try_min in attempts:
        clipped = text[: max(500, int(len(text) * ratio))]
        try:
            return summarize_with_hf_api(clipped, try_max, max(10, try_min))
        except ValueError as exc:
            last_error = str(exc)
            if "index out of range in self" not in last_error and "Timeout from" not in last_error:
                raise

    raise ValueError(f"Hugging Face API failed after retries: {last_error}")


# ---------- Pipeline ----------

def summarize_chunk(text: str, max_length: int, min_length: int, use_remote_api: bool) -> str:
    if use_remote_api:
        return summarize_with_hf_api_safe(text, max_length, min_length)
    return summarize_locally(text, max_length, min_length)


def summarize_text(text: str, length: str = "medium") -> Dict[str, object]:
    cleaned = clean_text(text)
    if not cleaned:
        raise ValueError("No text provided")

    truncated = len(cleaned) > MAX_INPUT_CHARS
    if truncated:
        cleaned = cleaned[:MAX_INPUT_CHARS]

    config = LENGTH_PRESETS.get(length, LENGTH_PRESETS["medium"])
    max_length, min_length = config["max_length"], config["min_length"]
    chunk_max = min(CHUNK_MAX_LENGTH, max_length)
    chunk_min = min(CHUNK_MIN_LENGTH, min_length)

    use_remote_api = should_use_remote_api()
    start_time = time.time()
    chunks = split_into_chunks(cleaned)

    if len(chunks) == 1:
        summary = summarize_chunk(cleaned, max_length, min_length, use_remote_api)
    else:
        # Map: summarize each chunk.
        chunk_summaries = [summarize_chunk(c, chunk_max, chunk_min, use_remote_api) for c in chunks]
        merged = " ".join(chunk_summaries)

        # Condense again if the combined summaries are still too long for one pass.
        if len(merged) > CHUNK_CHAR_SIZE:
            merged = " ".join(
                summarize_chunk(part, chunk_max, chunk_min, use_remote_api)
                for part in split_into_chunks(merged)
            )

        # Reduce: summarize the combined summaries into the final result.
        summary = summarize_chunk(merged, max_length, min_length, use_remote_api)

    processing_ms = int((time.time() - start_time) * 1000)

    return {
        "summary": summary,
        "bullets": extract_bullets(summary),
        "source_word_count": len(cleaned.split()),
        "summary_word_count": len(summary.split()),
        "chunk_count": len(chunks),
        "processing_ms": processing_ms,
        "mode": "hosted" if use_remote_api else "local",
        "model": get_hf_api_model_name() if use_remote_api else get_local_model_name(),
        "truncated": truncated,
    }