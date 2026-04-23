# Smart Web Summarizer

Beginner-friendly project using:

- Python
- Flask
- HuggingFace Transformers
- Chrome Extension API (Manifest V3)

## 1) Backend Setup

1. Open terminal in `backend/`
2. Create and activate a virtual environment
3. Install dependencies
4. Create `.env` file from `.env.example`

### Windows (PowerShell)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python app.py
```

Backend runs at: `http://127.0.0.1:5000`

Test health endpoint:

```powershell
curl http://127.0.0.1:5000/health
```

## 2) Load Extension in Chrome

1. Open `chrome://extensions`
2. Turn on **Developer mode**
3. Click **Load unpacked**
4. Select the `extension/` folder

## 3) Use It

1. Open an article page
2. Click extension icon
3. Choose summary length
4. Click **Summarize Current Page**

## 4) Troubleshooting

- If popup says backend error, ensure Flask server is running.
- First run may be slow because model downloads.
- If PowerShell blocks activation script, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 5) Avoid local model downloads

By default, `transformers` stores model files in your local cache.  
To avoid that, use hosted inference from Hugging Face:

Preferred: set these in `backend/.env` (auto-loaded on startup):

```env
USE_HF_INFERENCE_API=true
HF_API_TOKEN=hf_your_token_here
HF_API_MODEL=facebook/bart-large-cnn
HF_API_TIMEOUT_SECONDS=90
HF_API_RETRIES_PER_ENDPOINT=2
# Optional: comma-separated endpoint templates (must include {model})
# HF_INFERENCE_URLS=https://router.huggingface.co/hf-inference/models/{model}
```

Alternative: set temporary environment variables in PowerShell:

```powershell
$env:USE_HF_INFERENCE_API="true"
$env:HF_API_TOKEN="hf_your_token_here"
$env:HF_API_MODEL="facebook/bart-large-cnn"
python app.py
```

Notes:

- In this mode, the backend sends text to Hugging Face API and does not download model weights locally.
- You can change the hosted model with `HF_API_MODEL`.
- Increase `HF_API_TIMEOUT_SECONDS` if your network is slow.
- Increase `HF_API_RETRIES_PER_ENDPOINT` for unstable connections.
- If you switch back to local mode, unset `USE_HF_INFERENCE_API` or set it to `false`.

Optional (if you still use local mode): move cache to another drive

```powershell
$env:HF_HOME="D:\hf-cache"
$env:TRANSFORMERS_CACHE="D:\hf-cache"
python app.py
```

## 6) Next Improvements

- Summarize selected text only
- Add summary history with `chrome.storage`
- Deploy backend and switch extension URL
