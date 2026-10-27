const state = {
  config: null,
  participantId: "",
  sessionId: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`,
  queue: [],
  practiceQueue: [],
  currentTrial: null,
  trialIndex: 0,
  completedMain: 0,
  testMode: false,
  isPractice: false,
  practiceCompleted: false,
  resuming: false,
  syncResponse: null,
  naturalnessRating: null,
  videoStartMs: null,
  videoEndMs: null,
  syncQuestionStartMs: null,
  naturalnessQuestionStartMs: null,
  breakpoints: [],
};

const SESSION_STORAGE_PREFIX = "av_study_session_";
const LAST_PARTICIPANT_KEY = "av_study_last_pid";

function generateRandomParticipantId() {
  const num = Math.floor(10000 + Math.random() * 90000);
  return `P-${num}`;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (m) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[m]));
}

function getSavedSession(participantId) {
  if (!participantId) return null;
  const raw = localStorage.getItem(`${SESSION_STORAGE_PREFIX}${participantId.toLowerCase().trim()}`);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function persistSession() {
  if (!state.participantId || state.testMode) return;
  const data = {
    participantId: state.participantId,
    sessionId: state.sessionId,
    queue: state.queue,
    completedMain: state.completedMain,
    practiceCompleted: state.practiceCompleted || false,
    timestamp: Date.now(),
  };
  localStorage.setItem(
    `${SESSION_STORAGE_PREFIX}${state.participantId.toLowerCase().trim()}`,
    JSON.stringify(data)
  );
}

function clearPersistedSession(participantId) {
  if (!participantId) return;
  localStorage.removeItem(`${SESSION_STORAGE_PREFIX}${participantId.toLowerCase().trim()}`);
}

const $ = (id) => document.getElementById(id);

const screens = [
  "loading", "error", "welcome", "instructions", "practice-intro",
  "trial", "practice-complete", "break", "complete"
];

function showScreen(name) {
  screens.forEach(s => $(`screen-${s}`).classList.toggle("active", s === name));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function shuffle(array) {
  const out = [...array];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/*
  Randomization constraint:
  - shuffle all trials
  - then make a best-effort pass so identical base clips are not adjacent
*/
function constrainedShuffle(trials) {
  let best = shuffle(trials);
  for (let attempt = 0; attempt < 300; attempt++) {
    const candidate = shuffle(trials);
    let collisions = 0;
    for (let i = 1; i < candidate.length; i++) {
      if (candidate[i].base_clip_id === candidate[i - 1].base_clip_id) collisions++;
    }
    if (collisions === 0) return candidate;

    let bestCollisions = 0;
    for (let i = 1; i < best.length; i++) {
      if (best[i].base_clip_id === best[i - 1].base_clip_id) bestCollisions++;
    }
    if (collisions < bestCollisions) best = candidate;
  }
  return best;
}

async function loadConfig() {
  const res = await fetch("/api/config", { cache: "no-store" });
  if (!res.ok) throw new Error("Could not load study configuration.");
  return await res.json();
}

function validateConfig(config) {
  if (!config.stimuli.length) {
    return "No valid experimental clips were found.";
  }

  const invalid = config.stimuli.filter(x =>
    !x.base_clip_id || !x.category || !Number.isInteger(x.offset_ms)
  );
  if (invalid.length) {
    return "Some stimulus filenames could not be parsed.";
  }

  return null;
}

function updateProgress() {
  const wrap = $("progressWrap");
  if (state.testMode || state.isPractice || !state.queue.length) {
    wrap.classList.add("hidden");
    return;
  }

  wrap.classList.remove("hidden");
  const total = state.queue.length;
  const current = Math.min(state.completedMain + 1, total);
  $("progressText").textContent = `Trial ${current} of ${total}`;
  $("progressBar").style.width = `${(state.completedMain / total) * 100}%`;
}

function computeBreakpoints(total) {
  if (total < 12) return [];
  if (total >= 56) return [Math.round(total / 3), Math.round((2 * total) / 3)];
  return [Math.round(total / 2)];
}

function resetTrialUI() {
  $("screen-trial").classList.remove("questions-visible");
  $("syncQuestion").classList.add("hidden");
  $("naturalnessQuestion").classList.add("hidden");
  $("videoOverlay").classList.remove("hidden");
  $("videoEndedBadge").classList.add("hidden");
  document.querySelectorAll(".choice-btn").forEach(btn => btn.classList.remove("selected"));

  state.syncResponse = null;
  state.naturalnessRating = null;
  state.videoStartMs = null;
  state.videoEndMs = null;
  state.syncQuestionStartMs = null;
  state.naturalnessQuestionStartMs = null;
}

function beginTrial(trial, isPractice = false) {
  state.currentTrial = trial;
  state.isPractice = isPractice;

  resetTrialUI();
  showScreen("trial");
  updateProgress();

  $("trialPhasePill").textContent = state.testMode ? "Test trial" : (isPractice ? "Practice trial" : "Experimental trial");
  $("trialInstruction").textContent = "Watch the entire clip before responding.";

  const video = $("stimulusVideo");
  video.pause();
  video.removeAttribute("src");
  video.load();
  video.src = trial.url;
  video.controls = false;
  video.currentTime = 0;

  // Preload adjacent future trials to reduce waiting.
  if (!isPractice) {
    const next = state.queue[state.completedMain + 1];
    if (next) preloadVideo(next.url);
  }
}

function preloadVideo(url) {
  const v = document.createElement("video");
  v.preload = "auto";
  v.src = url;
}

async function saveCurrentTrial(playbackError = false) {
  if (state.testMode) return;

  const now = performance.now();
  const payload = {
    participant_id: state.participantId,
    session_id: state.sessionId,
    trial_index: state.isPractice ? -1 : state.completedMain + 1,
    base_clip_id: state.currentTrial.base_clip_id || "practice",
    category: state.currentTrial.category || "practice",
    offset_ms: Number.isInteger(state.currentTrial.offset_ms) ? state.currentTrial.offset_ms : 0,
    synchrony_response: state.syncResponse,
    naturalness_rating: state.naturalnessRating,
    response_time_sync_ms: state.syncQuestionStartMs ? Math.round((state.naturalnessQuestionStartMs - state.syncQuestionStartMs) * 10) / 10 : null,
    response_time_naturalness_ms: state.naturalnessQuestionStartMs ? Math.round((now - state.naturalnessQuestionStartMs) * 10) / 10 : null,
    video_playback_ms: (state.videoStartMs && state.videoEndMs) ? Math.round((state.videoEndMs - state.videoStartMs) * 10) / 10 : null,
    viewport_width: window.innerWidth,
    viewport_height: window.innerHeight,
    is_practice: state.isPractice ? 1 : 0,
    playback_error: playbackError ? 1 : 0,
  };

  try {
    const res = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error("Save failed");
    setStatus("Saved");
  } catch (err) {
    console.error(err);
    setStatus("Save retry needed", true);
    // Local fallback to reduce data loss risk.
    const fallback = JSON.parse(localStorage.getItem("av_sync_unsent") || "[]");
    fallback.push(payload);
    localStorage.setItem("av_sync_unsent", JSON.stringify(fallback));
  }
}

async function flushFallback() {
  const fallback = JSON.parse(localStorage.getItem("av_sync_unsent") || "[]");
  if (!fallback.length) return;

  const remaining = [];
  for (const payload of fallback) {
    try {
      const res = await fetch("/api/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) remaining.push(payload);
    } catch {
      remaining.push(payload);
    }
  }
  localStorage.setItem("av_sync_unsent", JSON.stringify(remaining));
}

function setStatus(text, danger = false) {
  $("statusDot").lastChild.textContent = ` ${text}`;
  $("statusDot").querySelector("i").style.background = danger ? "#ff8e9a" : "#7ef0c1";
}

async function finishTrial() {
  if (state.testMode) {
    setStatus("Test complete");
    showScreen("complete");
    return;
  }

  await saveCurrentTrial(false);

  if (state.isPractice) {
    state.practiceCompleted = true;
    persistSession();
    showScreen("practice-complete");
    $("progressWrap").classList.add("hidden");
    return;
  }

  state.completedMain++;
  persistSession();
  updateProgress();

  if (state.completedMain >= state.queue.length) {
    clearPersistedSession(state.participantId);
    try {
      localStorage.removeItem(LAST_PARTICIPANT_KEY);
    } catch {}
    $("progressBar").style.width = "100%";
    setTimeout(() => $("progressWrap").classList.add("hidden"), 450);
    const completedPid = $("completedParticipantId");
    if (completedPid) completedPid.textContent = state.participantId;
    $("completedTrials").textContent = state.completedMain;
    $("sessionCode").textContent = state.sessionId.slice(0, 8).toUpperCase();
    showScreen("complete");
    return;
  }

  if (state.breakpoints.includes(state.completedMain)) {
    showScreen("break");
    return;
  }

  beginTrial(state.queue[state.completedMain], false);
}

function attachEvents() {
  const participantId = $("participantId");
  const randomizeIdBtn = $("randomizeIdBtn");
  const consentCheck = $("consentCheck");
  const startBtn = $("startBtn");
  const discardSessionBtn = $("discardSessionBtn");

  function updateStartState() {
    const id = participantId.value.trim();
    const consentChecked = consentCheck.checked;
    const resumeBanner = $("resumeBanner");
    const resumeDesc = $("resumeDesc");
    const idNotice = $("idNotice");
    const idNoticeText = $("idNoticeText");

    if (id.length < 2) {
      if (resumeBanner) resumeBanner.classList.add("hidden");
      startBtn.textContent = "Continue";
      startBtn.disabled = true;
      state.resuming = false;
      if (idNotice) {
        idNotice.classList.add("warn");
        idNoticeText.innerHTML = `<strong>Participant ID required:</strong> Please enter your ID or click <em>New random ID</em> above to continue.`;
      }
      return;
    }

    if (idNotice) {
      idNotice.classList.remove("warn");
      idNoticeText.innerHTML = `<strong>Please note this ID (<code>${escapeHtml(id)}</code>):</strong> Save or write this ID down. If your session is interrupted or stopped in between, you can return and re-enter this ID to resume where you left off.`;
    }

    const saved = getSavedSession(id);
    if (saved && (saved.completedMain > 0 || saved.practiceCompleted)) {
      state.resuming = true;
      if (resumeBanner) resumeBanner.classList.remove("hidden");
      const total = (saved.queue && saved.queue.length) ? saved.queue.length : (state.config?.stimuli?.length || 30);
      const current = Math.min(saved.completedMain + 1, total);

      if (saved.completedMain >= total) {
        if (resumeDesc) resumeDesc.textContent = `All ${total} trials already completed for this participant.`;
        startBtn.textContent = "View Completion Screen";
      } else if (saved.completedMain > 0) {
        if (resumeDesc) resumeDesc.textContent = `Practice complete · Resuming from Trial ${current} of ${total}`;
        startBtn.textContent = `Resume Experiment (Trial ${current} of ${total})`;
      } else {
        if (resumeDesc) resumeDesc.textContent = `Practice complete · Ready for Trial 1 of ${total}`;
        startBtn.textContent = `Start Experiment (Trial 1 of ${total})`;
      }
      startBtn.disabled = !consentChecked;
    } else {
      state.resuming = false;
      if (resumeBanner) resumeBanner.classList.add("hidden");
      startBtn.textContent = "Continue";
      startBtn.disabled = !(id.length >= 2 && consentChecked);
    }
  }

  participantId.addEventListener("input", updateStartState);
  consentCheck.addEventListener("change", updateStartState);

  if (randomizeIdBtn) {
    randomizeIdBtn.addEventListener("click", () => {
      participantId.value = generateRandomParticipantId();
      updateStartState();
      participantId.focus();
    });
  }

  if (discardSessionBtn) {
    discardSessionBtn.addEventListener("click", (e) => {
      e.preventDefault();
      const id = participantId.value.trim();
      if (confirm(`Reset and discard previous progress for "${id}"?`)) {
        clearPersistedSession(id);
        try {
          localStorage.removeItem(LAST_PARTICIPANT_KEY);
        } catch {}
        participantId.value = generateRandomParticipantId();
        updateStartState();
      }
    });
  }

  startBtn.addEventListener("click", () => {
    state.participantId = participantId.value.trim();
    try {
      localStorage.setItem(LAST_PARTICIPANT_KEY, state.participantId);
    } catch {}

    if (state.resuming) {
      const saved = getSavedSession(state.participantId);
      if (saved) {
        state.sessionId = saved.sessionId || state.sessionId;
        if (saved.queue && saved.queue.length) state.queue = saved.queue;
        state.completedMain = saved.completedMain || 0;
        state.practiceCompleted = saved.practiceCompleted || false;
        state.isPractice = false;
        state.breakpoints = computeBreakpoints(state.queue.length);

        if (state.completedMain >= state.queue.length) {
          const completedPid = $("completedParticipantId");
          if (completedPid) completedPid.textContent = state.participantId;
          $("completedTrials").textContent = state.completedMain;
          $("sessionCode").textContent = state.sessionId.slice(0, 8).toUpperCase();
          showScreen("complete");
          return;
        }

        beginTrial(state.queue[state.completedMain], false);
        return;
      }
    }

    persistSession();
    showScreen("instructions");
  });

  if (!state.testMode && !participantId.value) {
    let initialId = "";
    try {
      const lastId = localStorage.getItem(LAST_PARTICIPANT_KEY);
      if (lastId && getSavedSession(lastId)) {
        initialId = lastId;
      }
    } catch {}

    if (!initialId) {
      initialId = generateRandomParticipantId();
    }
    participantId.value = initialId;
  }
  updateStartState();

  document.querySelectorAll("[data-back='welcome']").forEach(btn => {
    btn.addEventListener("click", () => showScreen("welcome"));
  });

  $("instructionsNextBtn").addEventListener("click", () => showScreen("practice-intro"));

  $("practiceStartBtn").addEventListener("click", () => {
    let practice = state.practiceQueue[0];
    if (!practice) {
      // If no dedicated practice clip exists, reuse the first experimental clip,
      // but mark/save it as practice. For the real study, add a separate practice file.
      practice = state.queue[0];
    }
    beginTrial(practice, true);
  });

  $("mainStartBtn").addEventListener("click", () => {
    state.isPractice = false;
    state.completedMain = 0;
    state.breakpoints = computeBreakpoints(state.queue.length);
    beginTrial(state.queue[0], false);
  });

  $("breakContinueBtn").addEventListener("click", () => {
    beginTrial(state.queue[state.completedMain], false);
  });

  const video = $("stimulusVideo");

  $("playBtn").addEventListener("click", async () => {
    $("videoOverlay").classList.add("hidden");
    state.videoStartMs = performance.now();
    try {
      await video.play();
      setStatus("Playing");
    } catch (err) {
      console.error(err);
      setStatus("Playback blocked", true);
      $("videoOverlay").classList.remove("hidden");
    }
  });

  video.addEventListener("ended", () => {
    state.videoEndMs = performance.now();
    $("videoEndedBadge").classList.remove("hidden");
    $("syncQuestion").classList.remove("hidden");
    $("screen-trial").classList.add("questions-visible");
    state.syncQuestionStartMs = performance.now();
    $("trialInstruction").textContent = "Answer the two questions below.";
    setStatus("Awaiting response");
  });

  video.addEventListener("error", async () => {
    console.error("Video playback error", video.error);
    setStatus("Video error", true);
  });

  function selectSyncResponse(val) {
    const btn = document.querySelector(`.sync-choice[data-value="${val}"]`);
    if (!btn) return;
    document.querySelectorAll(".sync-choice").forEach(x => x.classList.remove("selected"));
    btn.classList.add("selected");
    state.syncResponse = val;

    state.naturalnessQuestionStartMs = performance.now();
    $("naturalnessQuestion").classList.remove("hidden");
  }

  async function selectNaturalnessResponse(rating) {
    if (!state.syncResponse) return;
    const btn = document.querySelector(`.natural-choice[data-value="${rating}"]`);
    if (!btn) return;
    document.querySelectorAll(".natural-choice").forEach(x => x.classList.remove("selected"));
    btn.classList.add("selected");
    state.naturalnessRating = Number(rating);
    setStatus("Saving…");
    await new Promise(r => setTimeout(r, 140));
    await finishTrial();
  }

  document.querySelectorAll(".sync-choice").forEach(btn => {
    btn.addEventListener("click", () => selectSyncResponse(btn.dataset.value));
  });

  document.querySelectorAll(".natural-choice").forEach(btn => {
    btn.addEventListener("click", () => selectNaturalnessResponse(Number(btn.dataset.value)));
  });

  document.addEventListener("keydown", (e) => {
    if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;

    const trialActive = $("screen-trial") && $("screen-trial").classList.contains("active");
    if (!trialActive) return;

    // Space or Enter to play video when overlay is active
    if ((e.code === "Space" || e.code === "Enter") && !$("videoOverlay").classList.contains("hidden")) {
      e.preventDefault();
      $("playBtn").click();
      return;
    }

    // Y or N for Synchrony
    if (!$("syncQuestion").classList.contains("hidden") && !state.syncResponse) {
      if (e.key === "y" || e.key === "Y") {
        e.preventDefault();
        selectSyncResponse("yes");
        return;
      }
      if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        selectSyncResponse("no");
        return;
      }
    }

    // 1 to 7 for Naturalness
    if (!$("naturalnessQuestion").classList.contains("hidden") && state.syncResponse && !state.naturalnessRating) {
      if (["1", "2", "3", "4", "5", "6", "7"].includes(e.key)) {
        e.preventDefault();
        selectNaturalnessResponse(Number(e.key));
        return;
      }
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden && $("screen-trial").classList.contains("active")) {
      setStatus("Window changed", true);
    }
  });

  const submitDebriefBtn = $("submitDebriefBtn");
  if (submitDebriefBtn) {
    submitDebriefBtn.addEventListener("click", async () => {
      const feedback = $("debriefFeedback").value.trim();
      if (!feedback) return;
      submitDebriefBtn.disabled = true;
      try {
        await fetch("/api/debrief", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            participant_id: state.participantId,
            session_id: state.sessionId,
            feedback: feedback,
          }),
        });
        $("debriefStatus").style.display = "inline";
      } catch (e) {
        console.error("Failed to save debrief feedback", e);
        submitDebriefBtn.disabled = false;
      }
    });
  }

  const testAgainBtn = $("testAgainBtn");
  if (testAgainBtn) {
    testAgainBtn.addEventListener("click", () => {
      const alternatives = state.config.stimuli.filter(
        trial => !state.currentTrial || trial.filename !== state.currentTrial.filename
      );
      const next = shuffle(alternatives.length ? alternatives : state.config.stimuli)[0];
      beginTrial(next, false);
    });
  }
}

async function boot() {
  state.testMode = document.body.dataset.testMode === "true";
  attachEvents();
  if (!state.testMode) await flushFallback();

  try {
    state.config = await loadConfig();
    const error = validateConfig(state.config);
    if (error) {
      $("errorMessage").textContent = error;
      showScreen("error");
      return;
    }

    state.queue = constrainedShuffle(state.config.stimuli);
    state.practiceQueue = shuffle(state.config.practice);

    if (state.testMode) {
      state.participantId = "TEST";
      beginTrial(state.queue[0], false);
    } else {
      showScreen("welcome");
    }
    setStatus("Ready");
  } catch (err) {
    console.error(err);
    $("errorMessage").textContent = "The study configuration could not be loaded. Make sure the Flask server is running.";
    showScreen("error");
    setStatus("Offline", true);
  }
}

boot();
