const API_BASE = "http://127.0.0.1:5000";
const WORDS_PER_MINUTE = 238;
const MIN_TEXT_LENGTH = 120;
const MAX_TEXT_LENGTH = 50000;

const els = {
  backendStatus: document.getElementById("backendStatus"),
  backendText: document.getElementById("backendText"),
  button: document.getElementById("summarizeBtn"),
  status: document.getElementById("status"),
  loading: document.getElementById("loading"),
  result: document.getElementById("result"),
  pageTitle: document.getElementById("pageTitle"),
  summary: document.getElementById("summaryText"),
  bullets: document.getElementById("bullets"),
  truncatedNote: document.getElementById("truncatedNote"),
  savings: document.getElementById("savings"),
  copyBtn: document.getElementById("copyBtn"),
  meta: document.getElementById("meta"),
};

let lastResult = null;

/* ---------- UI helpers ---------- */

function setStatus(message = "", type = "info") {
  els.status.textContent = message;
  els.status.dataset.type = type;
}

function setBackend(state, text) {
  els.backendStatus.dataset.state = state;
  els.backendText.textContent = text;
}

function setLoading(isLoading) {
  els.button.disabled = isLoading;
  els.button.textContent = isLoading ? "Summarizing..." : "Summarize this page";
  els.loading.classList.toggle("hidden", !isLoading);
  if (isLoading) els.result.classList.add("hidden");
}

function formatNumber(n) {
  return typeof n === "number" ? n.toLocaleString() : "-";
}

/* ---------- Length preference ---------- */

function getSelectedLength() {
  return document.querySelector('input[name="length"]:checked')?.value || "medium";
}

function restoreLength() {
  try {
    const saved = localStorage.getItem("summaryLength");
    const input = saved && document.querySelector(`input[name="length"][value="${saved}"]`);
    if (input) input.checked = true;
  } catch {
    /* storage unavailable, keep default */
  }
}

document.querySelectorAll('input[name="length"]').forEach((input) => {
  input.addEventListener("change", () => {
    try {
      localStorage.setItem("summaryLength", input.value);
    } catch {
      /* ignore */
    }
  });
});

/* ---------- Backend health ---------- */

async function checkBackend() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3000);

  try {
    const res = await fetch(`${API_BASE}/health`, { signal: controller.signal });
    if (!res.ok) throw new Error("Health check failed");
    const data = await res.json();

    const label =
      data.mode === "hosted" ? "Hosted model" : data.mode === "local" ? "Local model" : "Backend ready";
    setBackend("online", label);
    return true;
  } catch {
    setBackend("offline", "Backend offline");
    setStatus("Start the backend with python app.py, then try again.", "error");
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/* ---------- Page text extraction ---------- */

async function getPageContent() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("Could not find the current tab.");

  const url = tab.url || "";
  if (/\.pdf($|[?#])/i.test(url)) {
    throw new Error("PDF files aren't supported yet. Open an article page instead.");
  }
  if (!/^https?:/i.test(url)) {
    throw new Error("This page can't be read. Open a regular website and try again.");
  }

  const [injection] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    args: [MAX_TEXT_LENGTH],
    func: (maxLength) => {
      const candidates = [...document.querySelectorAll("article, main, [role='main']")];
      const best = candidates
        .map((el) => el.innerText || "")
        .sort((a, b) => b.length - a.length)[0];
      const text = (best && best.length > 500 ? best : document.body?.innerText || "").trim();
      return { text: text.slice(0, maxLength), title: document.title };
    },
  });

  return injection?.result || { text: "", title: tab.title || "" };
}

/* ---------- Rendering ---------- */

function renderResult(data, title) {
  lastResult = data;

  els.pageTitle.textContent = title || "";
  els.summary.textContent = data.summary || "No summary returned.";

  els.bullets.replaceChildren();
  const points = Array.isArray(data.bullets) ? data.bullets : [];
  for (const point of points) {
    const li = document.createElement("li");
    const mark = document.createElement("mark");
    mark.textContent = point;
    li.appendChild(mark);
    els.bullets.appendChild(li);
  }

  els.truncatedNote.classList.toggle("hidden", !data.truncated);

  const source = data.source_word_count;
  const summaryWords = data.summary_word_count;
  if (typeof source === "number" && typeof summaryWords === "number") {
    const minutesSaved = Math.round((source - summaryWords) / WORDS_PER_MINUTE);
    els.savings.textContent =
      `${formatNumber(source)} words cut to ${formatNumber(summaryWords)}.` +
      (minutesSaved >= 1 ? ` About ${minutesSaved} min of reading saved.` : "");
  } else {
    els.savings.textContent = "";
  }

  const rows = [
    ["Mode", data.mode === "hosted" ? "Hosted API" : data.mode === "local" ? "Local" : "-"],
    ["Model", data.model || "-"],
    ["Chunks", formatNumber(data.chunk_count)],
    ["Time", typeof data.processing_ms === "number" ? `${(data.processing_ms / 1000).toFixed(1)} s` : "-"],
  ];
  els.meta.replaceChildren();
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = label;
    dd.textContent = value;
    els.meta.append(dt, dd);
  }

  els.result.classList.remove("hidden", "reveal");
  void els.result.offsetWidth; // restart the highlight animation
  els.result.classList.add("reveal");
  els.result.scrollTop = 0;
}

/* ---------- Actions ---------- */

async function summarizeCurrentPage() {
  setStatus("");
  setLoading(true);

  try {
    const { text, title } = await getPageContent();
    if (!text || text.length < MIN_TEXT_LENGTH) {
      throw new Error("This page doesn't have enough readable text to summarize.");
    }

    let response;
    try {
      response = await fetch(`${API_BASE}/summarize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, length: getSelectedLength() }),
      });
    } catch {
      setBackend("offline", "Backend offline");
      throw new Error("Can't reach the backend. Start it with python app.py and try again.");
    }

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `The backend returned an error (${response.status}).`);
    }

    setBackend("online", data.mode === "hosted" ? "Hosted model" : "Local model");
    renderResult(data, title);
  } catch (error) {
    setStatus(error.message || "Something went wrong.", "error");
  } finally {
    setLoading(false);
  }
}

async function copySummary() {
  if (!lastResult) return;

  const points = (lastResult.bullets || []).map((p) => `- ${p}`).join("\n");
  const text = `${lastResult.summary}${points ? `\n\nKey points:\n${points}` : ""}`;

  try {
    await navigator.clipboard.writeText(text);
    els.copyBtn.textContent = "Copied";
  } catch {
    els.copyBtn.textContent = "Copy failed";
  }
  setTimeout(() => (els.copyBtn.textContent = "Copy"), 1500);
}

els.button.addEventListener("click", summarizeCurrentPage);
els.copyBtn.addEventListener("click", copySummary);

restoreLength();
checkBackend();