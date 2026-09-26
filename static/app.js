const CAM_COLORS = {
  1: "#3b82f6", // Cam 1 Wide (Blue)
  2: "#10b981", // Cam 2 Vocal (Emerald)
  3: "#f59e0b", // Cam 3 Guitar Solo (Amber)
  4: "#a855f7", // Cam 4 Roaming (Purple)
};

let ws = null;
let isPaused = false;

function connectStream() {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${window.location.host}/ws/stream`);

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    renderDashboardFrame(data);
  };

  ws.onclose = () => {
    setTimeout(connectStream, 1500);
  };
}

function renderDashboardFrame(data) {
  const { timestamp, scene, audio, metrics, decision, atem, frames, switch_history } = data;

  // 1. Update Header & Scene Event Banner
  document.getElementById("timecodeDisplay").textContent = `${timestamp.toFixed(1).padStart(4, "0")}s / 30.0s`;
  document.getElementById("scenePhaseName").textContent = scene.phase_name;
  document.getElementById("sceneGtCams").textContent = `추천(GT) 카메라: Cam ${scene.gt_recommended_cams.join(", Cam ")}`;
  document.getElementById("atemBadge").textContent = `ATEM: ${atem.mode.toUpperCase()} | PGM: Cam ${atem.program_input} | Switches: ${atem.total_switches}`;

  const beatLamp = document.getElementById("beatLamp");
  beatLamp.classList.toggle("active", Boolean(audio.is_beat_peak));

  const soloLamp = document.getElementById("soloLamp");
  if (audio.solo_cam_id) {
    soloLamp.classList.add("active");
    soloLamp.textContent = `🎸 AUDIO SOLO BOOST → CAM ${audio.solo_cam_id}`;
  } else {
    soloLamp.classList.remove("active");
    soloLamp.textContent = "SOLO STANDBY";
  }

  // 2. Update Top Preview & Program Monitors
  const pgmCam = decision.program_cam;
  const pvwCam = decision.preview_cam;

  document.getElementById("pgmCamLabel").textContent = `CAM ${pgmCam}`;
  document.getElementById("pvwCamLabel").textContent = `CAM ${pvwCam}`;
  document.getElementById("smStateBadge").textContent = decision.state;
  document.getElementById("pgmReasonText").textContent = decision.switch_reason;
  document.getElementById("pvwReasonText").textContent = `차순위 후보 Cam ${pvwCam} (현재 카메라 대비 점수차: ${decision.candidate_score_gap >= 0 ? "+" : ""}${decision.candidate_score_gap.toFixed(2)})`;

  if (frames[String(pgmCam)]) {
    document.getElementById("pgmImage").src = `data:image/jpeg;base64,${frames[String(pgmCam)]}`;
  }
  if (frames[String(pvwCam)]) {
    document.getElementById("pvwImage").src = `data:image/jpeg;base64,${frames[String(pvwCam)]}`;
  }

  // Hold Progress Bar
  const minHold = parseFloat(document.getElementById("rngMinHold").value) || 2.5;
  const maxHold = parseFloat(document.getElementById("rngMaxHold").value) || 7.5;
  const shotDur = decision.current_shot_duration || 0.0;
  document.getElementById("shotDurText").textContent = `${shotDur.toFixed(1)}s`;
  document.getElementById("holdMinMarker").style.left = `${Math.min(95, (minHold / maxHold) * 100)}%`;
  document.getElementById("holdProgressFill").style.width = `${Math.min(100, (shotDur / maxHold) * 100)}%`;

  // 3. Update 4 Camera Cards
  for (let cid = 1; cid <= 4; cid++) {
    const m = metrics[String(cid)];
    const card = document.getElementById(`camCard${cid}`);
    card.classList.toggle("tally-pgm", cid === pgmCam);
    card.classList.toggle("tally-pvw", cid === pvwCam && cid !== pgmCam);

    if (frames[String(cid)]) {
      document.getElementById(`camImg${cid}`).src = `data:image/jpeg;base64,${frames[String(cid)]}`;
    }

    const badge = document.getElementById(`scoreTotal${cid}`);
    badge.textContent = m.is_blurry ? `BLUR (${m.composite_score.toFixed(2)})` : m.composite_score.toFixed(2);
    badge.style.color = m.is_blurry ? "#f87171" : "#38bdf8";

    setBar(`barF${cid}`, `valF${cid}`, m.framing_score, "#38bdf8");
    setBar(`barM${cid}`, `valM${cid}`, m.motion_score, "#f59e0b");
    setBar(`barS${cid}`, `valS${cid}`, m.sharpness_score, m.is_blurry ? "#ef4444" : "#22c55e");
  }

  // 4. Update Switch History Log
  if (switch_history && switch_history.length > 0) {
    const logBox = document.getElementById("switchLogBox");
    logBox.innerHTML = switch_history
      .slice()
      .reverse()
      .map(
        (ev) =>
          `<div class="log-item">[${ev.timestamp.toFixed(2)}s] <b>Cam ${ev.from_cam} → Cam ${ev.to_cam}</b> (${ev.state || ev.reason}) — ${ev.reason}</div>`
      )
      .join("");
  }
}

function setBar(barId, valId, score, color) {
  const bar = document.getElementById(barId);
  const val = document.getElementById(valId);
  if (bar) {
    bar.style.width = `${Math.round(score * 100)}%`;
    bar.style.background = color;
  }
  if (val) {
    val.textContent = score.toFixed(2);
  }
}

async function manualCut(targetCam) {
  await fetch("/api/atem/cut", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_cam: targetCam }),
  });
}

async function pushConfig() {
  const minHold = parseFloat(document.getElementById("rngMinHold").value);
  const maxHold = parseFloat(document.getElementById("rngMaxHold").value);
  const wFraming = parseFloat(document.getElementById("rngWFraming").value);
  const wMotion = parseFloat(document.getElementById("rngWMotion").value);
  const blurThresh = parseFloat(document.getElementById("rngBlurThresh").value);

  document.getElementById("txtMinHold").textContent = `${minHold.toFixed(1)}s`;
  document.getElementById("lblMinHold").textContent = minHold.toFixed(1);
  document.getElementById("txtMaxHold").textContent = `${maxHold.toFixed(1)}s`;
  document.getElementById("lblMaxHold").textContent = maxHold.toFixed(1);
  document.getElementById("txtWFraming").textContent = wFraming.toFixed(2);
  document.getElementById("txtWMotion").textContent = wMotion.toFixed(2);
  document.getElementById("txtBlurThresh").textContent = blurThresh.toFixed(0);

  await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      min_hold_sec: minHold,
      max_hold_sec: maxHold,
      w_framing: wFraming,
      w_motion: wMotion,
      blur_threshold: blurThresh,
      auto_director_enabled: document.getElementById("chkAutoDirector").checked,
      beat_sync_enabled: document.getElementById("chkBeatSync").checked,
    }),
  });
}

async function runSceneSelectionBenchmark() {
  const btn = document.getElementById("btnRunBenchmark");
  btn.textContent = "⏳ 30초 과업 평가 중...";
  btn.disabled = true;

  try {
    const activeModeBtn = document.querySelector(".mode-btn.active");
    const mode = activeModeBtn ? activeModeBtn.dataset.mode : "synthetic";
    const res = await fetch(`/api/benchmark/run?mode=${encodeURIComponent(mode)}`, { method: "POST" });
    const report = await res.json();

    const m = report.metrics;
    document.getElementById("kpiAccuracy").textContent = `${m.gt_alignment_accuracy_pct}%`;
    document.getElementById("kpiBlurAvoid").textContent = `${m.blur_avoidance_rate_pct}%`;
    document.getElementById("kpiMeanHold").textContent = `${m.mean_shot_duration_sec}s (${m.total_switches} cuts)`;
    document.getElementById("kpiFlicker").textContent = `${m.flicker_violations} 회`;

    // Render GT vs AI Timeline Strip
    const stripGT = document.getElementById("stripGT");
    const stripAI = document.getElementById("stripAI");
    stripGT.innerHTML = "";
    stripAI.innerHTML = "";

    report.timeline.forEach((item) => {
      const segGT = document.createElement("div");
      segGT.className = "tl-seg";
      const hasBlur = item.blur_cams && item.blur_cams.includes(4);
      segGT.style.background = hasBlur ? "#ef4444" : CAM_COLORS[item.gt_primary_cam] || "#64748b";
      segGT.title = `[${item.timestamp}s] ${item.phase_name} | 추천: Cam ${item.gt_allowed_cams.join(",")}`;
      stripGT.appendChild(segGT);

      const segAI = document.createElement("div");
      segAI.className = "tl-seg";
      segAI.style.background = CAM_COLORS[item.program_cam] || "#64748b";
      segAI.title = `[${item.timestamp}s] AI 선택: Cam ${item.program_cam} (${item.state})`;
      stripAI.appendChild(segAI);
    });
  } finally {
    btn.textContent = "▶ 30초 과업 벤치마크 평가 실행";
    btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  connectStream();
  runSceneSelectionBenchmark();

  // Mode Tabs
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document.querySelectorAll(".mode-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      await fetch("/api/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: btn.dataset.mode }),
      });
      runSceneSelectionBenchmark();
    });
  });

  // Pause Button
  document.getElementById("btnPause").addEventListener("click", async (e) => {
    isPaused = !isPaused;
    e.target.textContent = isPaused ? "▶ 재생 재개" : "⏸ 일시정지";
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paused: isPaused }),
    });
  });

  // Benchmark Button
  document.getElementById("btnRunBenchmark").addEventListener("click", runSceneSelectionBenchmark);

  // Sliders & Checkboxes
  ["rngMinHold", "rngMaxHold", "rngWFraming", "rngWMotion", "rngBlurThresh", "chkAutoDirector", "chkBeatSync"].forEach(
    (id) => {
      document.getElementById(id).addEventListener("input", pushConfig);
    }
  );

  // Upload Form
  document.getElementById("uploadForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    await fetch("/api/upload/quad", { method: "POST", body: formData });
    runSceneSelectionBenchmark();
  });
});
