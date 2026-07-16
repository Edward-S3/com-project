/* global fetch, VoiceSession */
const state = {
  userId: null,
  sessionId: null,
  meta: null,
  rubrics: null,
  ioMode: "text",
  voice: null,
  micOn: false,
  textOnly: false,
  partialEls: { user: null, partner: null },
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || res.statusText);
  }
  return res.json();
}

function showScreen(id) {
  document.querySelectorAll(".screen").forEach((el) => el.classList.remove("active"));
  document.getElementById(id).classList.add("active");
  document.querySelectorAll("#nav button").forEach((btn) => {
    btn.disabled = btn.dataset.screen > id && !state.userId;
  });
  ["s02", "s03", "s04", "s05"].forEach((s, i) => {
    const btn = document.querySelector(`[data-screen="${s}"]`);
    if (btn) btn.disabled = !state.userId && s !== "s01";
  });
}

function enableNav(from) {
  const order = ["s01", "s02", "s03", "s04", "s05"];
  const idx = order.indexOf(from);
  order.forEach((s, i) => {
    const btn = document.querySelector(`[data-screen="${s}"]`);
    if (btn) btn.disabled = i > idx;
  });
}

async function loadMeta() {
  state.meta = await api("/api/meta");
  fillSelect("dept-select", state.meta.departments, "id", "label");
  fillSelect("grade-select", state.meta.grades, "id", "label");
  fillSelect("scene-select", state.meta.scenes, "id", "label");
  fillSelect("role-select", state.meta.roles, "id", "label");
  fillSelect("difficulty-select", state.meta.difficulties, "id", "label");
  fillSelect("io-select", state.meta.io_modes, "id", "label");
}

function fillSelect(id, items, valKey, labelKey) {
  const sel = document.getElementById(id);
  sel.innerHTML = items.map((it) => `<option value="${it[valKey]}">${it[labelKey]}</option>`).join("");
}

async function loadUsers() {
  const users = await api("/api/users");
  const ul = document.getElementById("user-list");
  ul.innerHTML = users
    .map(
      (u) =>
        `<li><button type="button" data-user="${u.id}">${u.name}（${u.department} / ${u.grade}）</button></li>`
    )
    .join("");
  ul.querySelectorAll("button").forEach((btn) => {
    btn.onclick = () => {
      state.userId = Number(btn.dataset.user);
      enableNav("s02");
      showScreen("s02");
    };
  });
}

document.getElementById("user-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!document.getElementById("consent").checked) {
    alert("同意が必要です");
    return;
  }
  const fd = new FormData(e.target);
  const body = Object.fromEntries(fd.entries());
  body.age = Number(body.age);
  body.tenure_years = Number(body.tenure_years);
  body.consent = true;
  const res = await api("/api/users", { method: "POST", body: JSON.stringify(body) });
  state.userId = res.id;
  enableNav("s02");
  showScreen("s02");
};

document.getElementById("session-form").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {
    user_id: state.userId,
    scene: fd.get("scene"),
    role: fd.get("role"),
    difficulty: fd.get("difficulty"),
    io_mode: fd.get("io_mode"),
  };
  const res = await api("/api/sessions", { method: "POST", body: JSON.stringify(body) });
  state.sessionId = res.session_id;
  state.ioMode = res.io_mode || body.io_mode || "text";
  document.getElementById("mode-label").textContent = res.mode_code;
  document.getElementById("transcript").innerHTML = "";
  state.partialEls = { user: null, partner: null };
  appendMessage("partner", res.opening_message);
  loadFiveSteps();
  enableNav("s03");
  showScreen("s03");
  await setupIoMode(res);
};

async function setupIoMode(res) {
  const voiceBar = document.getElementById("voice-bar");
  if (state.voice) {
    await state.voice.stop();
    state.voice = null;
  }
  state.micOn = false;
  state.textOnly = state.ioMode !== "voice";
  if (state.ioMode !== "voice" || !res.ws_path) {
    voiceBar.hidden = true;
    return;
  }
  voiceBar.hidden = false;
  document.getElementById("voice-status").textContent = "音声: 接続中…";
  document.getElementById("mic-toggle").textContent = "マイク開始";
  document.getElementById("io-toggle").textContent = "テキスト入力へ";
  state.voice = new VoiceSession({
    wsPath: res.ws_path,
    onEvent: handleVoiceEvent,
  });
  try {
    await state.voice.start();
    document.getElementById("voice-status").textContent = "音声: 接続済み";
  } catch (err) {
    document.getElementById("voice-status").textContent = "音声: 接続失敗（HTTPS/マイクを確認）";
    console.error(err);
  }
}

function showJudgingLoading() {
  const overlay = document.getElementById("judging-overlay");
  const err = document.getElementById("judging-error");
  if (overlay) overlay.hidden = false;
  if (err) {
    err.hidden = true;
    err.textContent = "";
  }
  const endBtn = document.getElementById("end-btn");
  if (endBtn) endBtn.disabled = true;
}

function hideJudgingLoading() {
  const overlay = document.getElementById("judging-overlay");
  if (overlay) overlay.hidden = true;
  const endBtn = document.getElementById("end-btn");
  if (endBtn) endBtn.disabled = false;
}

function showJudgingError(message) {
  hideJudgingLoading();
  const err = document.getElementById("judging-error");
  if (err) {
    err.textContent = message;
    err.hidden = false;
  }
}

function handleVoiceEvent(msg) {
  if (msg.type === "status") {
    if (msg.status === "ready") {
      document.getElementById("voice-status").textContent = "音声: 準備完了";
    } else if (msg.status === "reconnecting") {
      document.getElementById("voice-status").textContent = "音声: 再接続中…";
    } else if (msg.status === "closed") {
      document.getElementById("voice-status").textContent = "音声: 切断";
      const overlay = document.getElementById("judging-overlay");
      if (overlay && !overlay.hidden) {
        showJudgingError("音声接続が切断されました。面談終了を再試行してください。");
      }
    }
  }
  if (msg.type === "audio") {
    document.getElementById("speaking-indicator").hidden = false;
  }
  if (msg.type === "interrupted") {
    document.getElementById("speaking-indicator").hidden = true;
    if (state.partialEls.partner) {
      state.partialEls.partner.classList.add("interrupted");
    }
  }
  if (msg.type === "transcript") {
    applyTranscript(msg);
  }
  if (msg.type === "ended" && msg.report) {
    document.getElementById("speaking-indicator").hidden = true;
    hideJudgingLoading();
    renderReport(msg.report);
    enableNav("s04");
    showScreen("s04");
    if (state.voice) state.voice.stop();
  }
  if (msg.type === "error") {
    document.getElementById("voice-status").textContent = `音声エラー: ${msg.message}`;
    const overlay = document.getElementById("judging-overlay");
    if (overlay && !overlay.hidden) {
      showJudgingError(`採点または音声接続でエラーが発生しました: ${msg.message}`);
    }
  }
}

function applyTranscript(msg) {
  const speaker = msg.speaker;
  if (msg.partial) {
    let el = state.partialEls[speaker];
    if (!el) {
      el = document.createElement("div");
      el.className = `msg ${speaker} partial`;
      const label = speaker === "user" ? "あなた" : "AI";
      el.innerHTML = `<div class="speaker">${label}</div><div class="text"></div>`;
      document.getElementById("transcript").appendChild(el);
      state.partialEls[speaker] = el;
    }
    el.querySelector(".text").textContent += msg.text;
    el.scrollIntoView({ behavior: "smooth" });
    return;
  }
  const partial = state.partialEls[speaker];
  if (partial) {
    partial.classList.remove("partial");
    partial.querySelector(".text").textContent = msg.text;
    if (msg.interrupted) partial.classList.add("interrupted");
    state.partialEls[speaker] = null;
  } else {
    appendMessage(speaker, msg.text, { interrupted: msg.interrupted });
  }
  if (msg.warnings) showWarnings(msg.warnings);
  if (speaker === "partner") {
    document.getElementById("speaking-indicator").hidden = true;
  }
}

function appendMessage(speaker, text, opts = {}) {
  const div = document.createElement("div");
  div.className = `msg ${speaker}`;
  if (opts.interrupted) div.classList.add("interrupted");
  const label = speaker === "user" ? "あなた" : "AI";
  div.innerHTML = `<div class="speaker">${label}</div><div class="text"></div>`;
  div.querySelector(".text").textContent = text;
  document.getElementById("transcript").appendChild(div);
  div.scrollIntoView({ behavior: "smooth" });
}

document.getElementById("mic-toggle").onclick = () => {
  if (!state.voice) return;
  if (state.micOn) {
    state.voice.stopMic();
    state.micOn = false;
    document.getElementById("mic-toggle").textContent = "マイク開始";
  } else {
    // Barge-in: stop local playback immediately when user starts speaking
    state.voice.stopPlayback();
    state.voice.startMic();
    state.micOn = true;
    document.getElementById("mic-toggle").textContent = "マイク停止";
    document.getElementById("speaking-indicator").hidden = true;
  }
};

document.getElementById("io-toggle").onclick = () => {
  state.textOnly = !state.textOnly;
  const btn = document.getElementById("io-toggle");
  if (state.textOnly) {
    btn.textContent = "音声入力へ";
    if (state.micOn && state.voice) {
      state.voice.stopMic();
      state.micOn = false;
      document.getElementById("mic-toggle").textContent = "マイク開始";
    }
  } else {
    btn.textContent = "テキスト入力へ";
  }
};

function showWarnings(warnings) {
  const el = document.getElementById("warnings");
  if (!warnings || !warnings.length) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = "<strong>参考警告</strong><ul>" + warnings.map((w) => `<li>${w}</li>`).join("") + "</ul>";
}

document.getElementById("chat-form").onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById("user-input");
  const text = input.value.trim();
  if (!text) return;
  appendMessage("user", text);
  input.value = "";
  if (state.ioMode === "voice" && state.voice && state.voice.ready && !state.textOnly) {
    state.voice.stopPlayback();
    state.voice.sendText(text);
    return;
  }
  if (state.ioMode === "voice" && state.voice && state.voice.ready && state.textOnly) {
    state.voice.sendText(text);
    return;
  }
  const res = await api(`/api/sessions/${state.sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
  showWarnings(res.warnings);
  if (res.partner_message) appendMessage("partner", res.partner_message);
  if (res.ended && res.report) {
    renderReport(res.report);
    enableNav("s04");
    showScreen("s04");
  }
};

document.getElementById("end-btn").onclick = async () => {
  if (state.ioMode === "voice" && state.voice && state.voice.ready) {
    showJudgingLoading();
    state.voice.endSession();
    return;
  }
  showJudgingLoading();
  try {
    const report = await api(`/api/sessions/${state.sessionId}/end`, { method: "POST" });
    hideJudgingLoading();
    renderReport(report);
    enableNav("s04");
    showScreen("s04");
  } catch (err) {
    showJudgingError(
      `採点に失敗しました。再試行してください。(${err && err.message ? err.message : "エラー"})`
    );
  }
};

function loadFiveSteps() {
  fetch("/static/rubrics_steps.json")
    .then((r) => r.json())
    .then((steps) => {
      document.getElementById("five-steps").innerHTML = steps
        .map((s) => `<li><strong>${s.name}</strong><ul>${s.points.map((p) => `<li>${p}</li>`).join("")}</ul></li>`)
        .join("");
    })
    .catch(() => {
      document.getElementById("five-steps").innerHTML = "<li>導入→趣旨→ふり返り→目標設定→クロージング</li>";
    });
}

function drawRadar(scores) {
  const canvas = document.getElementById("radar");
  const ctx = canvas.getContext("2d");
  const labels = Object.keys(scores).filter((k) => !k.startsWith("_"));
  const values = labels.map((k) => scores[k]);
  const n = labels.length;
  if (!n) return;
  const cx = 200;
  const cy = 200;
  const r = 140;
  ctx.clearRect(0, 0, 400, 400);
  ctx.strokeStyle = "#ccc";
  for (let level = 1; level <= 7; level++) {
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const a = (Math.PI * 2 * i) / n - Math.PI / 2;
      const rr = (r * level) / 7;
      const x = cx + rr * Math.cos(a);
      const y = cy + rr * Math.sin(a);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.stroke();
  }
  ctx.fillStyle = "rgba(30,58,95,0.35)";
  ctx.strokeStyle = "#1e3a5f";
  ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const a = (Math.PI * 2 * i) / n - Math.PI / 2;
    const rr = (r * values[i]) / 7;
    const x = cx + rr * Math.cos(a);
    const y = cy + rr * Math.sin(a);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#333";
  ctx.font = "11px sans-serif";
  labels.forEach((label, i) => {
    const a = (Math.PI * 2 * i) / n - Math.PI / 2;
    const x = cx + (r + 18) * Math.cos(a);
    const y = cy + (r + 18) * Math.sin(a);
    ctx.fillText(label.slice(0, 8), x - 20, y);
  });
}

function renderReport(report) {
  const scores = report.scores || {};
  const cleanScores = { ...scores };
  delete cleanScores._meta;
  drawRadar(cleanScores);
  const meta = scores._meta || {};
  let html = `<p><strong>総合評語:</strong> ${report.overall_grade || "-"}（平均 ${report.avg_score?.toFixed(1) ?? "-"}）</p>`;
  if (meta.goal_level_percent != null) {
    html += `<p><strong>目標水準:</strong> ${meta.goal_level_percent}% / 到達: ${meta.goal_level_reached}</p>`;
  }
  if (meta.feedback_flow_observed) {
    const flowLabels = {
      acknowledgment: "ねぎらい",
      negligence: "ねぎらい",
      deep_dive: "根拠深掘り",
      expectation: "期待水準提示",
    };
    const flow = meta.feedback_flow_observed;
    const parts = Object.entries(flow).map(
      ([k, v]) => `${flowLabels[k] || k}: ${v ? "実施" : "未実施"}`
    );
    html += `<p><strong>フィードバックフロー:</strong> ${parts.join(" / ")}</p>`;
  }
  html += `<h3>全体的な評価</h3><p>${report.overall_evaluation || ""}</p>`;
  html += `<h3>良い点</h3><ul>${(report.good_points || []).map((g) => `<li>${g.text}（「${g.quote || ""}」）</li>`).join("")}</ul>`;
  html += `<h3>改善点</h3><ul>${(report.improvements || []).map((g) => `<li>${g.text}（${g.principle || ""}）</li>`).join("")}</ul>`;
  html += `<h3>総評</h3><p>${report.summary || ""}</p>`;
  html += `<h3>模範例</h3><p>${report.model_answer || ""}</p>`;
  document.getElementById("report-content").innerHTML = html;
}

document.getElementById("to-history").onclick = async () => {
  await loadHistory();
  enableNav("s05");
  showScreen("s05");
};

document.getElementById("new-session").onclick = () => {
  showScreen("s02");
};

async function loadHistory() {
  const sessions = await api(`/api/users/${state.userId}/sessions`);
  const ul = document.getElementById("history-list");
  ul.innerHTML = sessions
    .map(
      (s) =>
        `<li>${s.started_at} | ${s.scene}/${s.role} | ${s.difficulty} | 評語:${s.overall_grade || "-"} 平均:${s.avg_score ?? "-"} <button data-sid="${s.id}">詳細</button></li>`
    )
    .join("");
  ul.querySelectorAll("button").forEach((btn) => {
    btn.onclick = async () => {
      state.sessionId = Number(btn.dataset.sid);
      const data = await api(`/api/sessions/${state.sessionId}`);
      if (data.report) {
        renderReport(data.report);
        enableNav("s04");
        showScreen("s04");
      }
    };
  });
}

document.querySelectorAll("#nav button").forEach((btn) => {
  btn.onclick = () => {
    if (!btn.disabled) showScreen(btn.dataset.screen);
  };
});

(async function init() {
  await loadMeta();
  await loadUsers();
})();
