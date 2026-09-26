const $ = (id) => document.getElementById(id);

const urlInput = $("urlInput");
const pasteBtn = $("pasteBtn");
const analyzeBtn = $("analyzeBtn");
const result = $("result");
const thumb = $("thumb");
const title = $("title");
const uploader = $("uploader");
const source = $("source");
const duration = $("duration");
const formats = $("formats");
const downloadBtn = $("downloadBtn");
const statusBox = $("status");
const liveBadge = document.querySelector(".live");

const API_BASE = (window.MX_CONFIG?.API_BASE || "").replace(/\/+$/, "");

let currentUrl = "";
let currentQuality = "";
let backendReady = false;

function apiUrl(path) {
  return API_BASE ? `${API_BASE}${path}` : path;
}

function setLiveBadge(state) {
  if (!liveBadge) return;
  const dot = liveBadge.querySelector("span");
  const label = state === "online" ? "ONLINE" : state === "checking" ? "CHECKING" : "BACKEND SETUP";
  liveBadge.childNodes.forEach((node) => {
    if (node.nodeType === Node.TEXT_NODE) node.remove();
  });
  liveBadge.append(` ${label}`);
  liveBadge.dataset.state = state;
  if (dot) dot.style.opacity = state === "online" ? "1" : state === "checking" ? ".55" : ".35";
}

async function checkBackend(showError = false) {
  setLiveBadge("checking");
  try {
    const res = await fetch(apiUrl("/api/health"), { cache: "no-store" });
    backendReady = res.ok;
  } catch {
    backendReady = false;
  }

  setLiveBadge(backendReady ? "online" : "setup");
  if (!backendReady && showError) {
    showStatus("The Cloudflare backend is not connected yet.", true);
  }
  return backendReady;
}

function fmtDuration(sec) {
  if (!Number.isFinite(sec)) return "";
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return h
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

function showStatus(message, error = false) {
  statusBox.textContent = message;
  statusBox.classList.remove("hidden", "error");
  if (error) statusBox.classList.add("error");
  clearTimeout(showStatus.timer);
  showStatus.timer = setTimeout(() => statusBox.classList.add("hidden"), 5000);
}

function setBusy(button, busy) {
  const text = button.querySelector(
    button === analyzeBtn ? ".btnText" : ".downloadText"
  );
  const spinner = button.querySelector(".spinner");
  button.disabled = busy;
  if (text) text.classList.toggle("hidden", busy);
  spinner?.classList.toggle("hidden", !busy);
}

async function api(path, options) {
  let res;
  try {
    res = await fetch(apiUrl(path), options);
  } catch {
    backendReady = false;
    setLiveBadge("setup");
    throw new Error("Could not reach the download backend.");
  }

  if (!res.ok) {
    if (res.status === 503) {
      backendReady = false;
      setLiveBadge("setup");
    }
    let msg = "Request failed.";
    try {
      const data = await res.json();
      msg = data.detail || msg;
    } catch {}
    throw new Error(msg);
  }

  backendReady = true;
  setLiveBadge("online");
  return res;
}

pasteBtn.addEventListener("click", async () => {
  try {
    urlInput.value = await navigator.clipboard.readText();
  } catch {
    showStatus("Clipboard permission was not available.", true);
  }
});

analyzeBtn.addEventListener("click", async () => {
  const url = urlInput.value.trim();
  if (!url) return showStatus("Paste a media URL first.", true);

  if (!backendReady && !(await checkBackend(true))) return;

  result.classList.add("hidden");
  setBusy(analyzeBtn, true);

  try {
    const res = await api("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });

    const data = await res.json();
    currentUrl = url;
    currentQuality = "";

    title.textContent = data.title || "Untitled media";
    uploader.textContent = data.uploader || "";
    source.textContent = (data.extractor || "MEDIA").toUpperCase();

    if (data.thumbnail) {
      thumb.src = data.thumbnail;
      thumb.style.visibility = "visible";
    } else {
      thumb.removeAttribute("src");
      thumb.style.visibility = "hidden";
    }

    const d = fmtDuration(data.duration);
    duration.textContent = d;
    duration.classList.toggle("hidden", !d);

    formats.innerHTML = "";
    for (const f of data.formats || []) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "format";
      b.textContent = f.label;
      b.dataset.id = f.id;
      b.addEventListener("click", () => {
        document.querySelectorAll(".format").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        currentQuality = f.id;
      });
      formats.appendChild(b);
    }

    const first = formats.querySelector(".format");
    if (first) first.click();
    else showStatus("No compatible downloadable formats were exposed by this source.", true);

    result.classList.remove("hidden");
    result.scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (err) {
    showStatus(err.message, true);
  } finally {
    setBusy(analyzeBtn, false);
  }
});

downloadBtn.addEventListener("click", async () => {
  if (!currentUrl || !currentQuality) {
    return showStatus("Choose a format first.", true);
  }

  if (!backendReady && !(await checkBackend(true))) return;

  setBusy(downloadBtn, true);
  showStatus("Preparing your file…");

  try {
    const res = await api("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: currentUrl, quality: currentQuality }),
    });

    const blob = await res.blob();
    const cd = res.headers.get("content-disposition") || "";
    const match = cd.match(/filename\*=UTF-8''([^;]+)|filename="?([^"]+)"?/i);
    const filename = decodeURIComponent(
      match?.[1] || match?.[2] || (currentQuality === "audio" ? "audio.mp3" : "video.mp4")
    );

    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);

    showStatus("Download ready.");
  } catch (err) {
    showStatus(err.message, true);
  } finally {
    setBusy(downloadBtn, false);
  }
});

urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") analyzeBtn.click();
});

checkBackend();
