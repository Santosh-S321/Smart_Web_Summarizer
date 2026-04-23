from __future__ import annotations

import os
import re
import time
from functools import lru_cache
from json import JSONDecodeError
from typing import Dict, List

import requests
from transformers import pipeline

MAX_INPUT_CHARS = 20000
CHUNK_CHAR_SIZE = 2200
CHUNK_OVERLAP = 200
HF_API_TIMEOUT_SECONDS = int(os.getenv("HF_API_TIMEOUT_SECONDS", "90"))
HF_API_RETRIES_PER_ENDPOINT = int(os.getenv("HF_API_RETRIES_PER_ENDPOINT", "2"))
DEFAULT_HF_INFERENCE_URLS = [
    "https://router.huggingface.co/hf-inference/models/{model}",
]


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


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


@lru_cache(maxsize=1)
def get_summarizer():
    return pipeline("summarization", model="sshleifer/distilbart-cnn-12-6")


def should_use_remote_api() -> bool:
    return os.getenv("USE_HF_INFERENCE_API", "false").lower() == "true"


def get_hf_inference_urls() -> List[str]:
    raw = os.getenv("HF_INFERENCE_URLS", "").strip()
    if not raw:
        return DEFAULT_HF_INFERENCE_URLS
    urls = [u.strip() for u in raw.split(",") if u.strip()]
    return urls or DEFAULT_HF_INFERENCE_URLS


def summarize_with_hf_api(text: str, max_length: int, min_length: int) -> str:
    token = os.getenv("HF_API_TOKEN", "").strip()
    if not token:
        raise ValueError("HF_API_TOKEN is required when USE_HF_INFERENCE_API=true")

    model = os.getenv("HF_API_MODEL", "facebook/bart-large-cnn")
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
        for attempt in range(HF_API_RETRIES_PER_ENDPOINT):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=HF_API_TIMEOUT_SECONDS)
            except requests.Timeout as exc:
                errors.append(f"Timeout from {url} (attempt {attempt + 1}/{HF_API_RETRIES_PER_ENDPOINT}): {exc}")
                continue
            except requests.RequestException as exc:
                errors.append(
                    f"Request failure from {url} (attempt {attempt + 1}/{HF_API_RETRIES_PER_ENDPOINT}): {exc}"
                )
                continue

            if response.status_code == 404:
                errors.append(f"404 from {url}: {response.text[:200]}")
                break
            if response.status_code >= 500:
                errors.append(f"{response.status_code} from {url}: {response.text[:200]}")
                continue
            if response.status_code >= 400:
                raise ValueError(f"Hugging Face API error: {response.status_code} {response.text}")

            try:
                data = response.json()
            except JSONDecodeError as exc:
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
            message = str(exc)
            last_error = message
            if "index out of range in self" not in message and "Timeout from" not in message:
                raise

    raise ValueError(f"Hugging Face API failed after retries: {last_error}")


def summarize_text(text: str, length: str = "medium") -> Dict[str, object]:
    cleaned = clean_text(text)
    if not cleaned:
        raise ValueError("No text provided")

    if len(cleaned) > MAX_INPUT_CHARS:
        cleaned = cleaned[:MAX_INPUT_CHARS]

    presets = {
        "short": {"max_length": 80, "min_length": 30},
        "medium": {"max_length": 140, "min_length": 50},
        "long": {"max_length": 220, "min_length": 80},
    }
    config = presets.get(length, presets["medium"])

    start_time = time.time()
    use_remote_api = should_use_remote_api()
    summarizer = None if use_remote_api else get_summarizer()
    chunks = split_into_chunks(cleaned)

    if len(chunks) == 1:
        if use_remote_api:
            summary = summarize_with_hf_api_safe(cleaned, config["max_length"], config["min_length"])
        else:
            result = summarizer(
                cleaned,
                max_length=config["max_length"],
                min_length=config["min_length"],
                do_sample=False,
            )
            summary = result[0]["summary_text"]
    else:
        chunk_summaries: List[str] = []
        for chunk in chunks:
            chunk_max = min(120, config["max_length"])
            chunk_min = min(45, config["min_length"])
            if use_remote_api:
                chunk_summary = summarize_with_hf_api_safe(chunk, chunk_max, chunk_min)
            else:
                chunk_result = summarizer(
                    chunk,
                    max_length=chunk_max,
                    min_length=chunk_min,
                    do_sample=False,
                )
                chunk_summary = chunk_result[0]["summary_text"]
            chunk_summaries.append(chunk_summary)

        merged = " ".join(chunk_summaries)
        if use_remote_api:
            if len(merged) > CHUNK_CHAR_SIZE:
                merged_parts = split_into_chunks(merged, CHUNK_CHAR_SIZE, CHUNK_OVERLAP)
                merged_summaries = [
                    summarize_with_hf_api_safe(
                        part,
                        min(120, config["max_length"]),
                        min(45, config["min_length"]),
                    )
                    for part in merged_parts
                ]
                merged = " ".join(merged_summaries)
            summary = summarize_with_hf_api_safe(merged, config["max_length"], config["min_length"])
        else:
            final_result = summarizer(
                merged,
                max_length=config["max_length"],
                min_length=config["min_length"],
                do_sample=False,
            )
            summary = final_result[0]["summary_text"]

    bullets = [s.strip() for s in re.split(r"(?<=[.!?])\s+", summary) if s.strip()][:5]
    processing_ms = int((time.time() - start_time) * 1000)

    return {
        "summary": summary,
        "bullets": bullets,
        "source_word_count": len(cleaned.split()),
        "summary_word_count": len(summary.split()),
        "chunk_count": len(chunks),
        "processing_ms": processing_ms,
    }
