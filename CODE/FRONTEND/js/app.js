"use strict";

const state = {
  user: null,
  page: "dashboard",
  activeJobId: null,
  analysisActive: false,
  analysisStatus: null,
  dashboard: null,
  recent: [],
  pollTimer: null,
  signalTimer: null,
  charts: { traffic: null, donut: null, resultTraffic: null, approach: null },
  selectedFile: null,
  sessionGeneration: 0,
  currentVideoJobId: null,
  currentVideoObjectUrl: null,
  liveStreamJobId: null,
  backendOnline: false,
  isPolling: false,
};

const isHttpPage = ["http:", "https:"].includes(window.location.protocol);
const API_BASE =
  isHttpPage && window.location.port === "8000"
    ? ""
    : `${isHttpPage ? window.location.protocol : "http:"}//${
        isHttpPage && window.location.hostname
          ? window.location.hostname
          : "127.0.0.1"
      }:8000`;

function buildUrl(url) {
  if (!url) return "";
  if (url.startsWith("http://") || url.startsWith("https://")) return url;
  return `${API_BASE}${url.startsWith("/") ? "" : "/"}${url}`;
}

const DIRECTIONS = ["NORTH", "EAST", "SOUTH", "WEST"];
const pageIds = {
  dashboard: "page-dashboard",
  live: "page-live",
  upload: "page-upload",
  results: "page-results",
  about: "page-about",
};

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", boot);

async function boot() {
  localStorage.removeItem("traffic_token");
  bindEvents();
  refreshIcons();
  setDate();
  await checkSession();
  startBackendMonitor();
}

function refreshIcons() {
  if (window.lucide?.createIcons) {
    window.lucide.createIcons();
  }
}

function setDate() {
  setText(
    "dateLabel",
    new Intl.DateTimeFormat("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    }).format(new Date())
  );
}

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value ?? "";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  }[c]));
}

let toastTimer = null;
function toast(message, type = "") {
  const el = $("toast");
  if (!el) return;
  el.textContent = message;
  el.className = `toast ${type} show`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

function newGeneration() {
  state.sessionGeneration += 1;
  return state.sessionGeneration;
}

function currentGen(generation) {
  return generation === state.sessionGeneration;
}

async function api(url, options = {}) {
  const fullUrl = buildUrl(url);
  const headers = { ...(options.headers || {}) };

  if (options.body instanceof FormData) {
    delete headers["Content-Type"];
  }

  const response = await fetch(fullUrl, {
    credentials: "include",
    cache: "no-store",
    ...options,
    headers,
  });

  let data = {};
  const type = response.headers.get("content-type") || "";

  if (type.includes("application/json")) {
    try {
      data = await response.json();
    } catch {
      data = {};
    }
  } else {
    try {
      const text = await response.text();
      if (text) data = { detail: text };
    } catch {
      /* ignore */
    }
  }

  if (!response.ok) {
    let detail = data.detail;
    if (Array.isArray(detail)) {
      detail = detail.map((x) => x?.msg || x?.message || String(x)).join(", ");
    }
    const error = new Error(detail || data.message || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }

  return data;
}

function bindEvents() {
  $("loginTab")?.addEventListener("click", () => showAuth("login"));
  $("registerTab")?.addEventListener("click", () => showAuth("register"));
  $("loginForm")?.addEventListener("submit", handleLogin);
  $("registerForm")?.addEventListener("submit", handleRegister);

  document.querySelectorAll(".password-toggle").forEach((button) => {
    button.addEventListener("click", () => togglePassword(button));
  });

  $("logoutBtn")?.addEventListener("click", handleLogout);
  $("mobileMenu")?.addEventListener("click", () => $("sidebar")?.classList.toggle("open"));

  document.querySelectorAll("[data-page]").forEach((el) => {
    el.addEventListener("click", () => showPage(el.dataset.page));
  });

  $("refreshDashboard")?.addEventListener("click", () => loadDashboard(true));
  $("startAnalysisTop")?.addEventListener("click", () => showPage("upload"));
  $("refreshResults")?.addEventListener("click", loadResults);
  $("newAnalysis")?.addEventListener("click", () => showPage("upload"));
  $("generateInsights")?.addEventListener("click", renderInsight);
  $("notificationButton")?.addEventListener("click", () =>
    toast(state.analysisActive ? `Analysis ${state.analysisStatus || "running"}.` : "No active analysis.", "info")
  );

  $("browseVideo")?.addEventListener("click", () => $("videoInput")?.click());
  $("uploadDropZone")?.addEventListener("click", (event) => {
    if (!event.target.closest("button")) $("videoInput")?.click();
  });
  $("videoInput")?.addEventListener("change", (event) => selectFile(event.target.files?.[0]));
  $("removeVideo")?.addEventListener("click", clearFile);
  $("startAnalysis")?.addEventListener("click", startAnalysis);
  $("cancelAnalysis")?.addEventListener("click", cancelActiveJob);
  $("cancelLiveJob")?.addEventListener("click", cancelActiveJob);
  $("refreshVideoHistory")?.addEventListener("click", loadPreviousVideos);
  $("downloadVideo")?.addEventListener("click", () => downloadResult("video"));
  $("downloadCsv")?.addEventListener("click", () => downloadResult("csv"));
  $("globalSearch")?.addEventListener("input", filterRecent);
  $("authAbout")?.addEventListener("click", () =>
    toast("AI Traffic Signal Control uses local YOLO detection, FastAPI, SQLite and adaptive four-way signal rules.", "info")
  );
  $("liveFrameStream")?.addEventListener("error", handleLiveStreamError);
}

function showAuth(mode) {
  $("loginPanel")?.classList.toggle("hidden", mode !== "login");
  $("registerPanel")?.classList.toggle("hidden", mode !== "register");
  $("loginTab")?.classList.toggle("active", mode === "login");
  $("registerTab")?.classList.toggle("active", mode === "register");
  setText("authError", "");
}

function togglePassword(button) {
  const targetId = button.dataset.target;
  const input = $(targetId);
  if (!input) return;
  input.type = input.type === "password" ? "text" : "password";
  button.innerHTML = `<i data-lucide="${input.type === "password" ? "eye" : "eye-off"}"></i>`;
  refreshIcons();
}

async function checkSession() {
  try {
    const data = await api("/api/auth/me");
    state.user = data.user;
    newGeneration();
    showApp();
    await Promise.allSettled([loadDashboard(), loadRecent(), loadPreviousVideos()]);
    startSignalSync();
  } catch {
    state.user = null;
    newGeneration();
    showAuthView();
  }
}

function showAuthView() {
  stopSignalSync();
  stopPolling();
  stopLiveStream();
  $("authView")?.classList.remove("hidden");
  $("appView")?.classList.add("hidden");
  showAuth("login");
}

function showApp() {
  $("authView")?.classList.add("hidden");
  $("appView")?.classList.remove("hidden");
  setUser();
}

function setUser() {
  if (!state.user) return;
  setText("userName", state.user.name || "User");
  setText("userEmail", state.user.email || "");
  setText("userAvatar", String(state.user.name || "U").charAt(0).toUpperCase());
}

async function handleLogin(event) {
  event.preventDefault();
  const email = $("loginEmail")?.value.trim();
  const password = $("loginPassword")?.value;
  if (!email || !password) {
    setText("authError", "Email and password are required.");
    return;
  }
  setButtonLoading("loginSubmit", true, "Signing in...");
  setText("authError", "");
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    state.user = data.user;
    newGeneration();
    showApp();
    toast("Signed in successfully", "success");
    await Promise.allSettled([loadDashboard(), loadRecent(), loadPreviousVideos()]);
    startSignalSync();
    showPage("dashboard");
  } catch (error) {
    setText("authError", error.message);
  } finally {
    setButtonLoading("loginSubmit", false, "Sign in");
  }
}

async function handleRegister(event) {
  event.preventDefault();
  const name = $("registerName")?.value.trim();
  const email = $("registerEmail")?.value.trim();
  const password = $("registerPassword")?.value;
  const confirm = $("registerConfirmPassword")?.value;

  if (!name || name.length < 2) {
    setText("authError", "Please enter your name.");
    return;
  }
  if (!password || password.length < 6) {
    setText("authError", "Password must contain at least 6 characters.");
    return;
  }
  if (password !== confirm) {
    setText("authError", "Passwords do not match.");
    return;
  }

  setButtonLoading("registerSubmit", true, "Creating account...");
  setText("authError", "");
  try {
    const data = await api("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, email, password, confirm_password: confirm }),
    });
    state.user = data.user;
    newGeneration();
    showApp();
    toast("Registration successful", "success");
    await Promise.allSettled([loadDashboard(), loadRecent(), loadPreviousVideos()]);
    startSignalSync();
    showPage("dashboard");
  } catch (error) {
    setText("authError", error.message);
  } finally {
    setButtonLoading("registerSubmit", false, "Create account");
  }
}

async function handleLogout() {
  newGeneration();
  cleanupAnalysis();
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch {
    /* session expired */
  }
  state.user = null;
  state.dashboard = null;
  state.recent = [];
  showAuthView();
  toast("Logged out", "success");
}

function setButtonLoading(id, loading, label) {
  const button = $(id);
  if (!button) return;
  button.disabled = loading;
  const span = button.querySelector("span");
  if (span) {
    span.textContent = loading ? label : id === "loginSubmit" ? "Sign in" : "Create account";
  } else {
    button.textContent = loading ? label : id === "loginSubmit" ? "Sign in" : "Create account";
  }
}

function showPage(page) {
  if (!state.user && page !== "about") return;
  Object.entries(pageIds).forEach(([key, id]) => {
    $(id)?.classList.toggle("active-page", key === page);
  });
  document.querySelectorAll(".nav-item").forEach((nav) => {
    nav.classList.toggle("active", nav.dataset.page === page);
  });
  state.page = page;
  $("sidebar")?.classList.remove("open");

  if (page === "dashboard") loadDashboard();
  if (page === "live") {
    renderLive();
    loadPreviousVideos();
  }
  if (page === "results") loadResults();
  if (page === "about") loadAbout();
}

async function loadDashboard(force = false) {
  if (!state.user) return;
  const generation = state.sessionGeneration;
  try {
    const data = await api("/api/dashboard/summary");
    if (!currentGen(generation)) return;
    state.dashboard = data;
    renderDashboard(data);
    if (force) toast("Dashboard refreshed", "success");
  } catch (error) {
    if (error.status === 401) {
      handleSessionExpired();
      return;
    }
    if (state.backendOnline) toast(error.message, "error");
  }
}

function renderDashboard(data) {
  if (!data) return;
  const active = data.active_job;
  const current = data.current;
  const model = data.model;

  if (model) {
    const notice = $("modelNotice");
    if (notice) {
      if (!model.ready) {
        notice.textContent = "YOLO model is not available. Place the configured pretrained model in MODELS before starting an analysis.";
        notice.classList.remove("hidden");
      } else {
        notice.classList.add("hidden");
      }
    }
  }

  if (active) {
    state.activeJobId = active.id;
    state.analysisActive = true;
    state.analysisStatus = active.status;
    if (!state.pollTimer) pollJob(active.id);
  } else if (!state.analysisActive) {
    state.activeJobId = null;
  }

  renderCurrent(current);
  renderCharts(data.trend || []);
  renderInsight();
  setStatusBadge(active ? active.status : current ? "completed" : "Idle");
}

function renderCurrent(row) {
  if (!row) {
    ["totalVehicles", "densityValue", "activeSignal", "greenTime", "signalSummaryPhase", "signalSummaryState", "signalSummaryTime"].forEach((id) =>
      setText(id, "—")
    );
    setText("activePhaseNote", "—");
    renderDirections(null);
    renderSignals(null);
    renderLiveMetrics(null);
    return;
  }
  setText("totalVehicles", row.total_count ?? "—");
  setText("densityValue", row.density || "—");
  setText("activeSignal", row.selected_phase || "—");
  setText("greenTime", `${Math.round(row.remaining_seconds ?? 0)}s`);
  setText("signalSummaryPhase", row.selected_phase || "—");
  setText("activePhaseNote", row.selected_phase ? `${row.signal_state || "—"} state` : "—");
  setText("signalSummaryState", row.signal_state || "—");
  setText("signalSummaryTime", `${Math.round(row.remaining_seconds ?? 0)}s`);
  renderSignals(row);
  renderDirections(row);
  renderLiveMetrics(row);
}

function renderSignals(row) {
  const active = row?.selected_phase;
  const stateValue = row ? String(row.signal_state || "RED").toUpperCase() : null;
  document.querySelectorAll(".traffic-light").forEach((light) => {
    const direction = light.dataset.direction;
    const directionState = !row ? null : direction === active ? stateValue : "RED";
    light.querySelectorAll("i[data-light]").forEach((lamp) => {
      lamp.classList.toggle(
        "active",
        directionState !== null && lamp.dataset.light === directionState
      );
    });
  });
  setText(
    "signalReason",
    row?.selected_phase
      ? `Decision: ${row.selected_phase} is prioritized using vehicle demand, density, estimated queue and waiting time.`
      : "No active signal decision."
  );
}

function renderDirections(row) {
  const container = $("directionGrid");
  if (!container) return;
  container.innerHTML = DIRECTIONS.map((direction) => {
    const key = direction.toLowerCase();
    const hasData = row !== null && row !== undefined;
    const count = hasData ? row[`${key}_count`] ?? "—" : "—";
    const ratio = hasData ? Number(row[`${key}_density`] ?? 0) : null;
    const density = ratio === null ? "—" : ratio < 0.3 ? "LOW" : ratio <= 0.65 ? "MEDIUM" : "HIGH";
    const queue = hasData && row[`${key}_queue`] !== undefined
      ? `Queue ≈ ${Math.round(Number(row[`${key}_queue`]) * 100)}%`
      : "Queue —";
    return `<div class="direction-card ${row?.selected_phase === direction ? "active" : ""}">
      <div class="direction-title">
        <strong>${direction}</strong>
        <span class="density-badge ${density === "—" ? "neutral" : density.toLowerCase()}">${density}</span>
      </div>
      <div class="direction-value">${count}</div>
      <div class="direction-meta">
        <span>Vehicles in frame</span>
        <span>${queue}</span>
      </div>
    </div>`;
  }).join("");
}

function renderLiveMetrics(row) {
  setText("livePhase", row?.selected_phase || "—");
  setText("liveGreen", row?.signal_state ? `${row.signal_state} · ${Math.round(row.remaining_seconds || 0)}s remaining` : "—");
  setText("liveVehicles", row?.total_count ?? "—");
  setText("liveDensity", row?.density || "—");

  const container = $("miniDirections");
  if (container) {
    container.innerHTML = DIRECTIONS.map((direction) => {
      const key = direction.toLowerCase();
      const hasData = row !== null && row !== undefined;
      const count = hasData ? row[`${key}_count`] ?? "—" : "—";
      const ratio = hasData ? Number(row[`${key}_density`] ?? 0) : null;
      const density = ratio === null ? "—" : ratio < 0.3 ? "LOW" : ratio <= 0.65 ? "MEDIUM" : "HIGH";
      return `<div class="mini-direction ${row?.selected_phase === direction ? "active" : ""}">
        <span>${direction}</span>
        <strong>${count}</strong>
        <small>${density}</small>
      </div>`;
    }).join("");
  }

  setText(
    "liveDecision",
    row?.selected_phase
      ? `Backend decision: ${row.selected_phase} is ${String(row.signal_state || "RED").toUpperCase()} with ${Math.round(
          row.remaining_seconds || 0
        )} seconds remaining.`
      : "No active decision."
  );
}

function setStatusBadge(status) {
  const element = $("signalStatusBadge");
  if (!element) return;
  element.className = `status-badge ${String(status || "neutral").toLowerCase()}`;
  element.textContent = status || "Idle";
}

function destroyChart(key) {
  if (state.charts[key]) {
    try {
      state.charts[key].destroy();
    } catch {
      /* ignore */
    }
    state.charts[key] = null;
  }
}

function renderCharts(rows) {
  if (!window.Chart) return;
  const labels = rows.map((row) => `${Number(row.timestamp || 0).toFixed(0)}s`);
  const values = rows.map((row) => row.total_count || 0);
  const averages = ["car_count", "motorcycle_count", "bus_count", "truck_count"].map((key) =>
    rows.length ? rows.reduce((sum, row) => sum + Number(row[key] || 0), 0) / rows.length : 0
  );

  destroyChart("traffic");
  destroyChart("donut");

  const trafficCanvas = $("trafficChart");
  if (trafficCanvas) {
    state.charts.traffic = new Chart(trafficCanvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Vehicles in frame",
            data: values,
            borderColor: "#4f46e5",
            backgroundColor: "rgba(79,70,229,.08)",
            fill: true,
            tension: 0.3,
            pointRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } },
      },
    });
  }

  const donutCanvas = $("vehicleDonut");
  if (donutCanvas) {
    state.charts.donut = new Chart(donutCanvas, {
      type: "doughnut",
      data: {
        labels: ["Cars", "Motorcycles", "Buses", "Trucks"],
        datasets: [
          {
            data: averages,
            borderWidth: 0,
            backgroundColor: ["#4f46e5", "#06b6d4", "#f59e0b", "#ef4444"],
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "72%",
        plugins: { legend: { display: false } },
      },
    });
  }

  const total = averages.reduce((a, b) => a + b, 0);
  setText("donutTotal", total ? total.toFixed(1) : "—");
  setText("legendCars", rows.length ? averages[0].toFixed(1) : "—");
  setText("legendBikes", rows.length ? averages[1].toFixed(1) : "—");
  setText("legendBuses", rows.length ? averages[2].toFixed(1) : "—");
  setText("legendTrucks", rows.length ? averages[3].toFixed(1) : "—");
}

function renderInsight() {
  const row = state.dashboard?.current;
  if (!row) {
    setText("insightContent", "Upload and analyze a traffic video to generate real traffic insights.");
    return;
  }
  const counts = DIRECTIONS.map((direction) => ({
    direction,
    count: Number(row[`${direction.toLowerCase()}_count`] || 0),
  })).sort((a, b) => b.count - a.count);

  const top = counts[0] || { direction: "—", count: 0 };
  setText(
    "insightContent",
    `${top.direction} currently has the highest vehicle demand at ${top.count} vehicles in frame. Overall density is ${
      row.density || "—"
    }. The adaptive controller selected ${row.selected_phase || "—"} in the ${row.signal_state || "—"} state with ${Math.round(
      row.remaining_seconds || 0
    )} seconds remaining. Queue values are estimated occupancy proxies.`
  );
}

function selectFile(file) {
  if (!file) return;
  const max = 500 * 1024 * 1024;
  const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
  const allowed = [".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"];

  if (!allowed.includes(ext)) {
    toast("Unsupported video format.", "error");
    return;
  }
  if (file.size > max) {
    toast("Video exceeds the 500 MB limit.", "error");
    return;
  }

  state.selectedFile = file;
  $("selectedFile")?.classList.remove("hidden");
  setText("selectedFileName", file.name);
  setText("selectedFileMeta", `${formatBytes(file.size)} · ${file.type || "video file"}`);
  if ($("startAnalysis")) $("startAnalysis").disabled = false;
}

function clearFile() {
  state.selectedFile = null;
  const input = $("videoInput");
  if (input) input.value = "";
  $("selectedFile")?.classList.add("hidden");
  if ($("startAnalysis")) $("startAnalysis").disabled = true;
}

async function startAnalysis() {
  if (!state.selectedFile || state.analysisActive) return;
  const generation = state.sessionGeneration;
  const form = new FormData();
  form.append("file", state.selectedFile);

  setButtonLoadingText("startAnalysis", "Uploading...");
  try {
    const data = await api("/api/analysis/upload", { method: "POST", body: form });
    if (!currentGen(generation)) return;
    state.activeJobId = data.job_id;
    state.analysisActive = true;
    state.analysisStatus = "queued";
    state.currentVideoJobId = null;
    state.dashboard = { ...(state.dashboard || {}), current: null, trend: [] };
    renderCurrent(null);
    renderCharts([]);

    toast("Analysis queued successfully", "success");
    showPage("live");
    showJobBox();
    startLiveStream(data.job_id);
    pollJob(data.job_id);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setButtonLoadingText("startAnalysis", "Start analysis");
  }
}

function setButtonLoadingText(id, text) {
  const button = $(id);
  if (!button) return;
  button.disabled = text.includes("...");
  button.innerHTML = `<i data-lucide="${text.includes("...") ? "loader-circle" : "play"}"></i> ${escapeHtml(text)}`;
  refreshIcons();
}

function showJobBox() {
  $("jobStatusBox")?.classList.remove("hidden");
  $("cancelLiveJob")?.classList.remove("hidden");
}

function pollJob(jobId) {
  if (!jobId) return;
  stopPolling();
  state.activeJobId = jobId;

  const run = async () => {
    if (!state.user || state.activeJobId !== jobId) return;
    if (state.isPolling) return;
    state.isPolling = true;

    try {
      const data = await api(`/api/analysis/jobs/${encodeURIComponent(jobId)}`);
      renderJobStatus(data);

      if (["completed", "failed", "cancelled"].includes(data.status)) {
        stopPolling();
        state.analysisActive = false;
        state.analysisStatus = data.status;

        if (data.status === "completed") {
          state.currentVideoJobId = jobId;
          stopLiveStream();
          attachVideo(jobId);
          await Promise.allSettled([loadPreviousVideos(), loadResults(), loadDashboard()]);
          toast("Analysis completed", "success");
        } else {
          stopLiveStream();
          setLiveIdleState(data.status);
          toast(data.message || `Analysis ${data.status}.`, data.status === "failed" ? "error" : "warning");
        }
        return;
      }

      state.analysisActive = true;
      state.analysisStatus = data.status;
      if (data.status === "running" || data.status === "queued") {
        startLiveStream(jobId);
      }
      state.pollTimer = setTimeout(run, 1000);
    } catch (error) {
      stopPolling();
      if (error.status === 401) {
        handleSessionExpired();
      } else {
        toast(error.message, "error");
      }
    } finally {
      state.isPolling = false;
    }
  };

  run();
}

function renderJobStatus(data) {
  showJobBox();
  const runtime = data.runtime || {};
  const status = data.status || runtime.status || "queued";
  const progressValue = data.progress !== undefined && data.progress !== null ? Number(data.progress) : Number(runtime.progress || 0) * 100;
  const progress = Math.max(0, Math.min(100, progressValue));

  setText("jobStatusTitle", status.charAt(0).toUpperCase() + status.slice(1));
  setText("jobStatusMessage", data.message || runtime.error || statusMessage(status));

  if ($("jobProgressFill")) $("jobProgressFill").style.width = `${progress}%`;
  setText("jobProgressText", `${Math.round(progress)}%`);
  setText("jobFrameText", `${runtime.frame || 0} / ${runtime.total_frames || 0} frames`);
  setText("videoStatus", status);
  setText("liveProgressText", `${Math.round(progress)}%`);
  if ($("liveProgressFill")) $("liveProgressFill").style.width = `${progress}%`;
  setText("liveFrameText", `${runtime.frame || 0} / ${runtime.total_frames || 0} frames`);
  setText("liveLastUpdate", runtime.updated_at ? `Last update ${formatDateTime(runtime.updated_at)}` : "Last update —");

  $("cancelAnalysis")?.classList.toggle("hidden", !["queued", "running"].includes(status));
  $("cancelLiveJob")?.classList.toggle("hidden", !["queued", "running"].includes(status));

  if (runtime.video) {
    setText("liveCurrentFile", runtime.video.filename || runtime.video.name || "Traffic video");
    setText("videoResolution", runtime.video.width && runtime.video.height ? `${runtime.video.width} × ${runtime.video.height}` : "—");
  }

  setText(
    "liveCurrentTitle",
    status === "completed"
      ? "Analysis completed"
      : status === "running"
      ? "Analysis in progress"
      : status === "queued"
      ? "Analysis queued"
      : `Analysis ${status}`
  );

  if (runtime.summary && Object.keys(runtime.summary).length) {
    const summary = runtime.summary;
    const signal = runtime.signal || {};
    const row = {
      total_count: summary.total,
      density: summary.density,
      selected_phase: signal.phase,
      signal_state: signal.state,
      green_seconds: signal.green_seconds,
      remaining_seconds: signal.remaining_seconds,
    };
    DIRECTIONS.forEach((direction) => {
      const key = direction.toLowerCase();
      row[`${key}_count`] = summary.approaches?.[direction] || 0;
      row[`${key}_density`] = summary.density_ratio?.[direction] || 0;
      row[`${key}_queue`] = signal.queues?.[direction] || 0;
    });
    state.dashboard = state.dashboard || {};
    state.dashboard.current = row;
    renderCurrent(row);
  } else {
    state.dashboard = { ...(state.dashboard || {}), current: null };
    renderCurrent(null);
  }
}

function statusMessage(status) {
  return (
    {
      queued: "Waiting for the analysis worker...",
      running: "YOLO is processing the traffic video...",
      completed: "Analysis completed.",
      cancelled: "Analysis cancelled.",
      failed: "Analysis failed.",
    }[status] || "Processing..."
  );
}

async function cancelActiveJob() {
  if (!state.activeJobId) return;
  try {
    await api(`/api/analysis/jobs/${encodeURIComponent(state.activeJobId)}/cancel`, { method: "POST" });
    toast("Cancellation requested", "warning");
  } catch (error) {
    toast(error.message, "error");
  }
}

function stopPolling() {
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
  state.isPolling = false;
}

function startSignalSync() {
  stopSignalSync();
  const run = async () => {
    if (!state.user) return;
    const generation = state.sessionGeneration;
    try {
      const data = await api("/api/signals/current");
      if (!currentGen(generation)) return;
      applySignalSnapshot(data);
    } catch (error) {
      if (error.status === 401) {
        handleSessionExpired();
        return;
      }
    } finally {
      if (state.user && currentGen(generation)) {
        state.signalTimer = setTimeout(run, 1500);
      }
    }
  };
  run();
}

function stopSignalSync() {
  if (state.signalTimer) {
    clearTimeout(state.signalTimer);
    state.signalTimer = null;
  }
}

function applySignalSnapshot(data) {
  if (!data) return;
  const signal = data.signal || {};
  const summary = data.summary || {};

  if (data.job_id && ["queued", "running"].includes(data.status)) {
    state.activeJobId = data.job_id;
    state.analysisActive = true;
    state.analysisStatus = data.status;
  }

  if (summary && Object.keys(summary).length) {
    const row = {
      total_count: summary.total ?? 0,
      density: summary.density || "—",
      selected_phase: signal.phase || null,
      signal_state: signal.state || "RED",
      green_seconds: signal.green_seconds ?? 0,
      remaining_seconds: signal.remaining_seconds ?? 0,
    };
    DIRECTIONS.forEach((direction) => {
      const key = direction.toLowerCase();
      row[`${key}_count`] = summary.approaches?.[direction] ?? 0;
      row[`${key}_density`] = summary.density_ratio?.[direction] ?? 0;
      row[`${key}_queue`] = signal.queues?.[direction] ?? 0;
    });
    state.dashboard = state.dashboard || {};
    state.dashboard.current = row;
    renderCurrent(row);
  } else {
    state.dashboard = { ...(state.dashboard || {}), current: null };
    renderCurrent(null);
  }

  if (signal.phase) {
    renderSignals({ selected_phase: signal.phase, signal_state: signal.state || "RED" });
    setText("activeSignal", signal.phase);
    setText("greenTime", `${Math.round(signal.remaining_seconds || 0)}s`);
    setText("signalSummaryPhase", signal.phase);
    setText("signalSummaryState", signal.state || "RED");
    setText("signalSummaryTime", `${Math.round(signal.remaining_seconds || 0)}s`);
  }
}

function cleanupAnalysis() {
  stopPolling();
  stopSignalSync();
  stopLiveStream();
  state.activeJobId = null;
  state.analysisActive = false;
  state.analysisStatus = null;
  state.currentVideoJobId = null;
  const video = $("trafficVideo");
  if (video) {
    clearProcessedVideo(video);
    video.classList.add("hidden");
  }
}

function clearProcessedVideo(video) {
  video.pause();
  video.removeAttribute("src");
  video.load();
  if (state.currentVideoObjectUrl) {
    URL.revokeObjectURL(state.currentVideoObjectUrl);
    state.currentVideoObjectUrl = null;
  }
}

function startLiveStream(jobId) {
  if (!jobId || state.liveStreamJobId === jobId) return;
  const image = $("liveFrameStream");
  if (!image) return;

  state.liveStreamJobId = jobId;
  image.classList.remove("hidden");
  $("videoFallback")?.classList.add("hidden");
  $("trafficVideo")?.classList.add("hidden");
  image.src = buildUrl(`/api/analysis/jobs/${encodeURIComponent(jobId)}/stream`);

  const indicator = $("liveFrameStatus");
  if (indicator) {
    indicator.textContent = "Live annotated frames";
    indicator.className = "stream-indicator live";
  }
}

function stopLiveStream() {
  const image = $("liveFrameStream");
  if (image) {
    image.removeAttribute("src");
    image.classList.add("hidden");
  }
  state.liveStreamJobId = null;
  const indicator = $("liveFrameStatus");
  if (indicator) {
    indicator.textContent = "Waiting for frames";
    indicator.className = "stream-indicator";
  }
}

let streamRetryTimer = null;
function handleLiveStreamError() {
  if (!state.analysisActive) return;
  const jobId = state.activeJobId;
  if (!jobId || state.liveStreamJobId !== jobId) return;

  clearTimeout(streamRetryTimer);
  streamRetryTimer = setTimeout(() => {
    if (state.analysisActive && state.activeJobId === jobId) {
      state.liveStreamJobId = null;
      startLiveStream(jobId);
    }
  }, 2000);
}

async function attachVideo(jobId) {
  const video = $("trafficVideo");
  if (!video || !jobId) return;

  stopLiveStream();
  $("videoFallback")?.classList.add("hidden");
  $("liveFrameStream")?.classList.add("hidden");
  clearProcessedVideo(video);

  video.onerror = () => {
    const err = video.error;
    if (err) {
      console.error("HTML5 Video Error Code:", err.code, "Message:", err.message);
      if (err.code === 4) {
        toast("Video format/codec not supported by browser. Backend must encode with H.264.", "error");
      }
    }
  };

  video.dataset.jobId = jobId;
  video.classList.remove("hidden");
  video.style.display = "block";
  video.controls = true;

  setText("videoResolution", "Processed output");
  const indicator = $("liveFrameStatus");
  if (indicator) {
    indicator.textContent = "Loading processed video";
    indicator.className = "stream-indicator complete";
  }

  try {
    const response = await fetch(
      buildUrl(`/api/results/video?job_id=${encodeURIComponent(jobId)}`),
      { credentials: "include", cache: "no-store" }
    );
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `Video request failed (${response.status})`);
    }

    const videoBlob = await response.blob();
    if (state.currentVideoJobId !== jobId) return;
    state.currentVideoObjectUrl = URL.createObjectURL(videoBlob);
    video.src = state.currentVideoObjectUrl;
    video.load();

    if (indicator) indicator.textContent = "Processed video ready";
    video.play().catch(() => {
      video.muted = true;
      video.play().catch((error) => console.warn("Autoplay blocked:", error));
    });
  } catch (error) {
    if (state.currentVideoJobId !== jobId) return;
    if (indicator) indicator.textContent = "Unable to load processed video";
    toast(`Unable to load processed video: ${error.message}`, "error");
  }
}

function setLiveIdleState(status = "idle") {
  stopLiveStream();
  $("trafficVideo")?.classList.add("hidden");
  $("videoFallback")?.classList.remove("hidden");
  setText(
    "liveCurrentTitle",
    status === "cancelled" ? "Analysis cancelled" : status === "failed" ? "Analysis failed" : "No active analysis"
  );
  setText(
    "liveCurrentFile",
    status === "cancelled"
      ? "The processing job was cancelled."
      : status === "failed"
      ? "The processing job failed. Check the status message for details."
      : "Upload a traffic video to begin monitoring."
  );
}

function renderLive() {
  if (state.dashboard?.current) renderCurrent(state.dashboard.current);
  if (state.analysisActive && state.activeJobId) {
    startLiveStream(state.activeJobId);
  } else if (state.currentVideoJobId) {
    attachVideo(state.currentVideoJobId);
  } else {
    setLiveIdleState();
  }
}

async function loadRecent() {
  try {
    const data = await api("/api/results/recent");
    state.recent = data.jobs || [];
    renderRecent(state.recent);
  } catch (error) {
    if (error.status !== 401) toast(error.message, "error");
  }
}

function renderRecent(rows) {
  const container = $("recentTable");
  if (!container) return;
  if (!rows || !rows.length) {
    container.innerHTML = `<div class="empty-state">No traffic analysis available yet. Upload a video to begin.</div>`;
    return;
  }
  container.innerHTML = `<table class="data-table">
    <thead>
      <tr><th>Job</th><th>Video</th><th>Status</th><th>Vehicles</th><th>Density</th><th>Phase</th><th>Created</th></tr>
    </thead>
    <tbody>
      ${rows
        .map(
          (row) => `<tr>
            <td>${escapeHtml((row.id || "").slice(0, 10))}</td>
            <td>${escapeHtml(row.original_filename || "Traffic video")}</td>
            <td><span class="status-badge ${escapeHtml(row.status)}">${escapeHtml(row.status)}</span></td>
            <td>${row.total_vehicles ?? "—"}</td>
            <td>${row.density || "—"}</td>
            <td>${row.phase || "—"}</td>
            <td>${formatDateTime(row.created_at)}</td>
          </tr>`
        )
        .join("")}
    </tbody>
  </table>`;
}

function filterRecent() {
  const query = $("globalSearch")?.value.toLowerCase().trim() || "";
  if (!query) {
    renderRecent(state.recent);
    return;
  }
  renderRecent(state.recent.filter((row) => JSON.stringify(row).toLowerCase().includes(query)));
}

async function loadPreviousVideos() {
  const container = $("previousVideos");
  if (!container || !state.user) return;
  try {
    const data = await api("/api/results/history");
    const runs = (data.runs || []).filter((run) => run.status === "completed");
    if (!runs.length) {
      container.innerHTML = `<div class="history-empty">No completed processed videos yet. Upload a traffic video and start an analysis.</div>`;
      return;
    }
    container.innerHTML = runs.map(renderPreviousVideoCard).join("");
    container.querySelectorAll("[data-play-run]").forEach((button) =>
      button.addEventListener("click", () => selectPreviousRun(button.dataset.playRun))
    );
    container.querySelectorAll("[data-download-video]").forEach((button) =>
      button.addEventListener("click", () => downloadJobFile(button.dataset.downloadVideo, "video"))
    );
    container.querySelectorAll("[data-download-csv]").forEach((button) =>
      button.addEventListener("click", () => downloadJobFile(button.dataset.downloadCsv, "csv"))
    );
    container.querySelectorAll("[data-delete-run]").forEach((button) =>
      button.addEventListener("click", () => deletePreviousRun(button.dataset.deleteRun, button))
    );
    refreshIcons();
  } catch (error) {
    container.innerHTML = `<div class="history-empty">Unable to load processed video history: ${escapeHtml(error.message)}</div>`;
  }
}

function renderPreviousVideoCard(run) {
  const filename = run.original_filename || "Traffic video";
  return `<article class="previous-video-card">
    <div class="previous-video-head">
      <div>
        <div class="previous-video-name">${escapeHtml(filename)}</div>
        <div class="previous-video-meta">${escapeHtml(formatDateTime(run.created_at))} · Job ${escapeHtml((run.id || "").slice(0, 10))}</div>
      </div>
      <span class="status-badge completed">Completed</span>
    </div>
    <div class="previous-video-stats">
      <div class="previous-video-stat"><span>Frames</span><strong>${run.total_frames ?? "—"}</strong></div>
      <div class="previous-video-stat"><span>Avg vehicles</span><strong>${run.average_vehicle_count ?? "—"}</strong></div>
      <div class="previous-video-stat"><span>Final phase</span><strong>${escapeHtml(run.final_phase || "—")}</strong></div>
      <div class="previous-video-stat"><span>Density</span><strong>${escapeHtml(run.final_density || "—")}</strong></div>
    </div>
    <div class="previous-video-meta">Final signal: ${escapeHtml(run.final_signal_state || "—")} · Completed: ${escapeHtml(formatDateTime(run.completed_at))}</div>
    <div class="previous-video-actions">
      <button class="primary-btn small" type="button" data-play-run="${escapeHtml(run.id)}"><i data-lucide="play"></i> Play video</button>
      <button class="secondary-btn small" type="button" data-download-video="${escapeHtml(run.id)}"><i data-lucide="download"></i> Download video</button>
      <button class="secondary-btn small" type="button" data-download-csv="${escapeHtml(run.id)}"><i data-lucide="file-spreadsheet"></i> CSV</button>
      <button class="danger-btn small" type="button" data-delete-run="${escapeHtml(run.id)}"><i data-lucide="trash-2"></i> Delete</button>
    </div>
  </article>`;
}

async function deletePreviousRun(jobId, button) {
  if (!jobId || !window.confirm("Delete this saved analysis, including its processed video and results?")) {
    return;
  }

  if (button) button.disabled = true;
  try {
    await api(`/api/results/${encodeURIComponent(jobId)}`, { method: "DELETE" });
    if (state.currentVideoJobId === jobId) {
      state.currentVideoJobId = null;
      const video = $("trafficVideo");
      if (video) clearProcessedVideo(video);
      setLiveIdleState();
    }
    state.recent = state.recent.filter((run) => run.id !== jobId);
    renderRecent(state.recent);
    toast("Saved analysis deleted", "success");
    await Promise.allSettled([
      loadPreviousVideos(),
      loadDashboard(),
      loadResults(),
    ]);
  } catch (error) {
    toast(`Unable to delete saved analysis: ${error.message}`, "error");
    if (button) button.disabled = false;
  }
}

function selectPreviousRun(jobId) {
  state.currentVideoJobId = jobId;
  state.analysisActive = false;
  state.analysisStatus = "completed";
  state.activeJobId = null;

  stopPolling();
  showPage("live");
  attachVideo(jobId);

  setText("liveCurrentTitle", "Previous analysis");
  const run = state.recent.find((item) => item.id === jobId);
  setText("liveCurrentFile", run?.original_filename || "Processed traffic video");
  setText("videoStatus", "completed");
}

function downloadJobFile(jobId, type) {
  if (!jobId) return;
  window.location.href = buildUrl(`/api/results/${type}?job_id=${encodeURIComponent(jobId)}`);
}

async function loadResults() {
  await loadRecent();
  const completed = state.recent.find((row) => row.status === "completed");
  if (!completed) {
    $("resultsEmpty")?.classList.remove("hidden");
    $("resultsContent")?.classList.add("hidden");
    return;
  }
  try {
    const data = await api(`/api/results/${encodeURIComponent(completed.id)}`);
    renderResultDetail(data);
    renderResultsHistory(state.recent);
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderResultsHistory(rows) {
  const container = $("resultsHistory");
  if (!container) return;
  if (!rows || !rows.length) {
    container.innerHTML = `<div class="history-empty">No analysis history.</div>`;
    return;
  }
  container.innerHTML = `<table class="data-table">
    <thead>
      <tr><th>Video</th><th>Status</th><th>Frames</th><th>Average vehicles</th><th>Final phase</th><th>Created</th></tr>
    </thead>
    <tbody>
      ${rows
        .map(
          (row) => `<tr>
            <td>${escapeHtml(row.original_filename || "Traffic video")}</td>
            <td><span class="status-badge ${escapeHtml(row.status)}">${escapeHtml(row.status)}</span></td>
            <td>${row.total_frames ?? "—"}</td>
            <td>${row.average_vehicle_count ?? "—"}</td>
            <td>${escapeHtml(row.phase || "—")}</td>
            <td>${escapeHtml(formatDateTime(row.created_at))}</td>
          </tr>`
        )
        .join("")}
    </tbody>
  </table>`;
}

function renderResultDetail(data) {
  $("resultsEmpty")?.classList.add("hidden");
  $("resultsContent")?.classList.remove("hidden");
  const job = data.job || {};
  const latest = data.metrics || {};
  setText("resultPeak", data.max_vehicles ?? 0);
  setText("resultDensity", latest.density || "—");
  setText("resultPhase", latest.selected_phase || "—");
  setText("resultFrames", job.total_frames ?? data.rows?.length ?? 0);

  const rows = data.rows || [];
  destroyChart("resultTraffic");
  destroyChart("approach");

  if (window.Chart) {
    const resultTrafficCanvas = $("resultTrafficChart");
    if (resultTrafficCanvas) {
      state.charts.resultTraffic = new Chart(resultTrafficCanvas, {
        type: "line",
        data: {
          labels: rows.map((row) => `${Number(row.timestamp || 0).toFixed(0)}s`),
          datasets: [
            {
              label: "Vehicles",
              data: rows.map((row) => row.total_count || 0),
              borderColor: "#4f46e5",
              tension: 0.3,
              pointRadius: 0,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
        },
      });
    }

    const approachCanvas = $("approachChart");
    if (approachCanvas) {
      state.charts.approach = new Chart(approachCanvas, {
        type: "bar",
        data: {
          labels: DIRECTIONS,
          datasets: [
            {
              label: "Average vehicles",
              data: DIRECTIONS.map((dir) => data.average_metrics?.[`${dir.toLowerCase()}_count`] || 0),
              backgroundColor: ["#4f46e5", "#06b6d4", "#f59e0b", "#ef4444"],
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { y: { beginAtZero: true } },
        },
      });
    }
  }

  const detailsContainer = $("resultDetails");
  if (detailsContainer) {
    detailsContainer.innerHTML = [
      ["Job ID", job.id || "—"],
      ["Status", job.status || "—"],
      ["Created", formatDateTime(job.created_at)],
      ["Completed", formatDateTime(job.completed_at)],
      ["Vehicles are", "Frame-level detections"],
      ["Queue metric", "Estimated occupancy proxy"],
    ]
      .map(([label, value]) => `<div class="detail-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
      .join("");
  }

  const downloadVidBtn = $("downloadVideo");
  if (downloadVidBtn) downloadVidBtn.dataset.jobId = job.id || "";
  const downloadCsvBtn = $("downloadCsv");
  if (downloadCsvBtn) downloadCsvBtn.dataset.jobId = job.id || "";

  state.currentVideoJobId = job.id;
}

function downloadResult(type) {
  const el = $(type === "video" ? "downloadVideo" : "downloadCsv");
  const id = el?.dataset.jobId;
  if (!id) {
    toast("No completed result selected.", "warning");
    return;
  }
  window.location.href = buildUrl(`/api/results/${type}?job_id=${encodeURIComponent(id)}`);
}

async function loadAbout() {
  try {
    const data = await api("/api/about");
    if (data.configuration) {
      setText("aboutMinGreen", `${data.configuration.min_green}s`);
      setText("aboutMaxGreen", `${data.configuration.max_green}s`);
      setText("aboutYellow", `${data.configuration.yellow_time}s`);
    }
  } catch {
    /* fallback content stays in place */
  }
}

function handleSessionExpired() {
  newGeneration();
  cleanupAnalysis();
  state.user = null;
  showAuthView();
  toast("Your session expired. Please sign in again.", "warning");
}

function startBackendMonitor() {
  const check = async () => {
    try {
      const data = await api("/api/health");
      state.backendOnline = true;
      const statusPill = $("backendStatus");
      if (statusPill) statusPill.className = "status-pill online";
      setText("backendStatus", data.model_ready ? "Backend online" : "Backend online · model missing");
      setText("systemStatusText", data.model_ready ? "Backend online" : "Model missing");
    } catch {
      state.backendOnline = false;
      const statusPill = $("backendStatus");
      if (statusPill) statusPill.className = "status-pill offline";
      setText("backendStatus", "Backend offline");
      setText("systemStatusText", "Backend offline");
    }
  };
  check();
  setInterval(check, 10000);
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 3);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(String(value).replace(" ", "T"));
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

window.TrafficApp = {
  getState: () => ({ ...state }),
  showPage,
  logout: handleLogout,
  refreshDashboard: loadDashboard,
};