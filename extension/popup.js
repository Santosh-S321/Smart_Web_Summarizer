const summarizeBtn = document.getElementById("summarizeBtn");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const summaryTextEl = document.getElementById("summaryText");
const bulletsEl = document.getElementById("bullets");
const metaEl = document.getElementById("meta");
const lengthEl = document.getElementById("length");

const BACKEND_URL = "http://127.0.0.1:5000/summarize";

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.style.color = isError ? "#b42318" : "#444";
}

function renderResult(data) {
  resultEl.classList.remove("hidden");
  summaryTextEl.textContent = data.summary || "No summary returned.";
  bulletsEl.innerHTML = "";

  const bullets = Array.isArray(data.bullets) ? data.bullets : [];
  for (const point of bullets) {
    const li = document.createElement("li");
    li.textContent = point;
    bulletsEl.appendChild(li);
  }

  metaEl.textContent =
    `Source words: ${data.source_word_count ?? "-"} | ` +
    `Summary words: ${data.summary_word_count ?? "-"} | ` +
    `Chunks: ${data.chunk_count ?? "-"} | ` +
    `Time: ${data.processing_ms ?? "-"}ms`;
}

async function getCurrentTabText() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("Could not find active tab.");

  const injectionResult = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      const main = document.querySelector("article, main");
      const text = (main?.innerText || document.body?.innerText || "").trim();
      return text.slice(0, 50000);
    },
  });

  return injectionResult?.[0]?.result || "";
}

async function summarizeCurrentPage() {
  summarizeBtn.disabled = true;
  resultEl.classList.add("hidden");
  setStatus("Extracting page text...");

  try {
    const text = await getCurrentTabText();
    if (!text || text.length < 120) {
      throw new Error("Not enough readable text on this page.");
    }

    setStatus("Summarizing with backend model...");
    const response = await fetch(BACKEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, length: lengthEl.value }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Backend error");
    }

    renderResult(data);
    setStatus("Done.");
  } catch (error) {
    setStatus(error.message || "Something went wrong.", true);
  } finally {
    summarizeBtn.disabled = false;
  }
}

summarizeBtn.addEventListener("click", summarizeCurrentPage);
