/* Best Before — client */
(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const answerEl = $("#answer");
  const verdictEl = $("#verdict");
  const claimsEl = $("#claims");
  const claimsSection = $("#claims-section");
  const samplesEl = $("#samples");
  const hintEl = $("#input-hint");
  const countEl = $("#char-count");
  const checkBtn = $("#check");
  const clearBtn = $("#clear");
  const toastEl = $("#toast");

  const state = {
    samples: [],
    sampleId: null,
    report: null,
    busy: false,
    modes: { tavily: false, bifrost: false, direct: false },
  };

  const TONE = {
    current: "var(--fresh)",
    expired: "var(--stale)",
    stale: "var(--stale)",
    needs_correction: "var(--warn)",
    recheck: "var(--warn)",
    uncertain: "var(--warn)",
    unverified: "var(--quiet)",
  };

  const STANCE = {
    supports: { icon: "✓", label: "Supports" },
    contradicts: { icon: "✕", label: "Contradicts" },
    unrelated: { icon: "·", label: "Context" },
  };

  const GAUGE_C = 2 * Math.PI * 50;

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[character]));
  }

  function normalize(value) {
    return String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
  }

  function fmtDate(value) {
    if (!value) return "undated";
    const date = new Date(String(value).length === 10 ? `${value}T00:00:00Z` : value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  }

  function expiryLabel(verdict) {
    if (!verdict.best_before) return "No estimate";
    const when = fmtDate(verdict.best_before);
    if (verdict.best_before_kind === "expired") return `Expired by ${when}`;
    if (verdict.best_before_kind === "provisional") return `Recheck after ${when}`;
    return `Best before ${when}`;
  }

  function toast(message) {
    toastEl.textContent = message;
    toastEl.classList.add("show");
    window.clearTimeout(toast._timer);
    toast._timer = window.setTimeout(() => toastEl.classList.remove("show"), 2600);
  }

  function setChip(element, tone, text, title) {
    element.dataset.tone = tone;
    element.title = title || text;
    element.lastElementChild.textContent = text;
  }

  function updateCount() {
    const characters = answerEl.value.length;
    countEl.textContent = `${characters.toLocaleString()} character${characters === 1 ? "" : "s"}`;
  }

  function setHint(text, tone) {
    hintEl.textContent = text;
    if (tone) hintEl.dataset.tone = tone;
    else delete hintEl.dataset.tone;
  }

  function defaultHint() {
    if (state.modes.tavily) return "Paste any answer — claims are checked against live web search.";
    return "No Tavily key: the three samples use recorded evidence. Custom text will come back unverified.";
  }

  /* ------------------------------------------------------------- samples */

  function renderSamples() {
    samplesEl.innerHTML = state.samples.map((sample) => `
      <button class="sample" type="button" data-id="${esc(sample.id)}" aria-pressed="false"
              title="${esc(sample.blurb)}">
        <span class="sample-title">${esc(sample.title)}</span>
        <span class="sample-meta">${esc((sample.written || "").slice(0, 4))} · ${sample.claims} claims</span>
      </button>
    `).join("");
  }

  function setActiveSample(id) {
    samplesEl.querySelectorAll(".sample").forEach((button) => {
      button.setAttribute("aria-pressed", button.dataset.id === id ? "true" : "false");
    });
  }

  function selectSample(id) {
    const sample = state.samples.find((row) => row.id === id);
    if (!sample) return;
    state.sampleId = sample.id;
    answerEl.value = sample.answer;
    updateCount();
    setActiveSample(sample.id);
    setHint(`Loaded “${sample.title}”, written ${fmtDate(sample.written)}.`, "loaded");
  }

  /* -------------------------------------------------------------- render */

  function renderLoading() {
    verdictEl.setAttribute("aria-busy", "true");
    verdictEl.innerHTML = `
      <div class="loading">
        <div class="loading-title">Checking freshness…</div>
        <ol class="loading-steps" id="loading-steps">
          <li data-live="true">Split into claims</li>
          <li>Retrieve current sources</li>
          <li>Judge each claim</li>
          <li>Score and date the answer</li>
        </ol>
      </div>`;
    const steps = verdictEl.querySelectorAll("#loading-steps li");
    let index = 0;
    window.clearInterval(renderLoading._timer);
    renderLoading._timer = window.setInterval(() => {
      if (index < steps.length) {
        steps[index].dataset.done = "true";
        delete steps[index].dataset.live;
        index += 1;
        if (index < steps.length) steps[index].dataset.live = "true";
      } else {
        window.clearInterval(renderLoading._timer);
      }
    }, 620);
  }

  function renderError(message) {
    verdictEl.setAttribute("aria-busy", "false");
    verdictEl.innerHTML = `
      <div class="verdict">
        <div class="error-box"><b>Check failed</b>${esc(message)}</div>
        <p class="hint">Nothing was scored. Try again, or load a sample.</p>
      </div>`;
    claimsSection.hidden = true;
    window.clearInterval(renderLoading._timer);
  }

  function renderVerdict(report) {
    const verdict = report.verdict;
    const mode = report.mode;
    const tone = TONE[verdict.status] || "var(--quiet)";
    const counts = verdict.counts;
    const unsettled = (counts.uncertain || 0) + (counts.unverified || 0);
    const offset = GAUGE_C * (1 - Math.max(0, Math.min(100, verdict.score)) / 100);

    let judgeText;
    if (mode.judge === "bifrost") {
      const routed = mode.gateway.provider && mode.gateway.model && !mode.gateway.model.startsWith(mode.gateway.provider + "/")
        ? `${mode.gateway.provider}/${mode.gateway.model}`
        : (mode.gateway.model || "gateway");
      judgeText = `Bifrost → ${esc(routed)}`;
      if (mode.gateway.is_fallback) judgeText += " <b>(fallback route)</b>";
    } else if (mode.judge === "direct") {
      judgeText = "direct Groq";
    } else if (mode.judge === "recorded") {
      judgeText = "recorded verdicts (no model)";
    } else if (mode.judge === "heuristic") {
      judgeText = "heuristic verdicts (no model)";
    } else {
      judgeText = "not judged";
    }

    let evidenceText;
    if (mode.evidence === "tavily") evidenceText = "live Tavily search";
    else if (mode.evidence === "recorded") evidenceText = `recorded · ${fmtDate(mode.recorded_on)}`;
    else evidenceText = "no evidence retrieved";

    verdictEl.setAttribute("aria-busy", "false");
    verdictEl.innerHTML = `
      <div class="verdict" data-status="${esc(verdict.status)}">
        <span class="stamp" data-status="${esc(verdict.status)}">${esc(verdict.label)}</span>

        <div class="verdict-top">
          <svg class="gauge" viewBox="0 0 120 120" role="img"
               aria-label="Freshness ${verdict.score} out of 100">
            <circle class="gauge-track" cx="60" cy="60" r="50" fill="none" stroke-width="7"/>
            <circle class="gauge-fill" id="gauge-fill" cx="60" cy="60" r="50" fill="none" stroke-width="7"
                    stroke-linecap="round" stroke="${tone}"
                    stroke-dasharray="${GAUGE_C.toFixed(2)}"
                    stroke-dashoffset="${GAUGE_C.toFixed(2)}"
                    transform="rotate(-90 60 60)"/>
          </svg>
          <div>
            <p class="verdict-kicker">Freshness score</p>
            <div class="verdict-number" style="color:${tone}">${verdict.score}<span>/ 100</span></div>
          </div>
        </div>

        <h3>${esc(verdict.headline)}</h3>
        <p class="verdict-blurb">${esc(verdict.blurb)}</p>

        <dl class="verdict-facts">
          <div><dt>Best before</dt><dd>${esc(expiryLabel(verdict))}</dd></div>
          <div><dt>Stale claims</dt><dd>${counts.stale} of ${counts.total}</dd></div>
          <div><dt>Current</dt><dd>${counts.current}</dd></div>
          <div><dt>Unsettled</dt><dd>${unsettled}</dd></div>
        </dl>

        <p class="mode-note">
          <b>Evidence</b> ${evidenceText}<br>
          <b>Judge</b> ${judgeText} · <b>extraction</b> ${esc(mode.extraction)}<br>
          <b>Checked</b> ${fmtDate(report.checked_at.slice(0, 10))} · ${(report.timings.total_ms / 1000).toFixed(1)}s
          ${mode.gateway.used ? " · <b>gateway routing proven</b>" : ""}
        </p>

        ${mode.disclaimer ? `<p class="disclaimer">${esc(mode.disclaimer)}</p>` : ""}

        <div class="verdict-actions">
          <button class="btn ghost" type="button" id="copy-report">Copy JSON</button>
          <button class="btn ghost" type="button" id="download-report">Download</button>
          <button class="btn ghost" type="button" id="print-report">Print</button>
        </div>

        <div class="trace">
          <details>
            <summary>Pipeline · ${report.trace.length} steps</summary>
            <ol>
              ${report.trace.map((step) => `
                <li><b>${esc(step.step)}</b><span>${esc(step.label)} · ${step.ms} ms</span></li>
              `).join("")}
            </ol>
          </details>
        </div>
      </div>`;

    requestAnimationFrame(() => {
      const fill = $("#gauge-fill");
      if (fill) fill.style.strokeDashoffset = offset.toFixed(2);
    });

    $("#copy-report").addEventListener("click", copyReport);
    $("#download-report").addEventListener("click", downloadReport);
    $("#print-report").addEventListener("click", () => window.print());
  }

  function sourceRow(source) {
    const stance = STANCE[source.stance] || STANCE.unrelated;
    return `
      <li class="source" data-stance="${esc(source.stance)}">
        <div class="source-head">
          <span class="stance-icon" title="${stance.label}" aria-label="${stance.label}">${stance.icon}</span>
          <a class="source-title" href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.title)}</a>
          <span class="source-meta">${esc(source.domain)}</span>
          <span class="source-meta">${fmtDate(source.published_date)}</span>
          <span class="source-meta auth">authority ${Number(source.authority).toFixed(2)}</span>
        </div>
        ${source.content ? `<p class="excerpt">${esc(source.content)}</p>` : ""}
      </li>`;
  }

  function claimCard(claim, index, openEvidence) {
    const stanceLabel = claim.status === "stale" ? "Stale" : claim.status === "current" ? "Current"
      : claim.status === "uncertain" ? "Recheck" : "Unverified";
    const sourceList = claim.sources.map(sourceRow).join("");
    const current = claim.current_value
      ? `<div class="claim-current"><span class="label">What is current</span><p>${esc(claim.current_value)}</p></div>`
      : "";
    const note = claim.note ? `<p class="claim-note">${esc(claim.note)}</p>` : "";
    const details = claim.sources.length
      ? `<details ${openEvidence ? "open" : ""}><summary>Evidence (${claim.sources.length})</summary>
           <ul class="sources">${sourceList}</ul></details>`
      : `<p class="claim-note">No sources were retrieved for this claim.</p>`;

    return `
      <article class="claim" data-status="${esc(claim.status)}">
        <div class="claim-head">
          <div class="claim-id">${String(index + 1).padStart(2, "0")}</div>
          <div>
            <p class="claim-text">${esc(claim.text)}</p>
            <div class="claim-tags">
              <span class="tag kind">${esc(claim.kind_label)}</span>
              <span class="tag status">${esc(stanceLabel)}</span>
              <span class="tag">${claim.sources.length} source${claim.sources.length === 1 ? "" : "s"}</span>
            </div>
          </div>
          <div class="claim-score"><strong>${claim.score}</strong><br><span>/100</span></div>
        </div>
        <div class="bar" role="img" aria-label="Freshness ${claim.score} of 100">
          <div class="bar-fill" style="--pct:${Math.max(0, Math.min(100, claim.score))}%"></div>
          <span class="tick" style="left:45%" data-label="0.45"></span>
          <span class="tick" style="left:72%" data-label="0.72"></span>
        </div>
        ${current}
        ${note}
        ${details}
      </article>`;
  }

  function renderClaims(report) {
    let opened = false;
    claimsEl.innerHTML = report.claims.map((claim, index) => {
      const open = !opened && claim.status === "stale" && claim.sources.length > 0;
      if (open) opened = true;
      return claimCard(claim, index, open);
    }).join("");
    claimsSection.hidden = false;
  }

  function renderReport(report) {
    state.report = report;
    renderVerdict(report);
    renderClaims(report);
    if (report.mode.evidence === "tavily") {
      setChip($("#chip-evidence"), "live", "Tavily live", "Claims were checked against live web search");
    } else if (report.mode.evidence === "recorded") {
      setChip($("#chip-evidence"), "recorded", `Recorded · ${fmtDate(report.mode.recorded_on)}`,
        "Evidence was recorded from the cited pages; no live search was used");
    } else {
      setChip($("#chip-evidence"), "off", "No evidence", "No Tavily key and no recorded evidence for this text");
    }
    if (report.mode.judge === "bifrost") {
      setChip($("#chip-gateway"), "live", "Bifrost gateway", "Model calls were routed through Bifrost");
    } else if (report.mode.judge === "direct") {
      setChip($("#chip-gateway"), "recorded", "Direct · Groq", "Bifrost was not reachable; calls went to Groq directly");
    } else {
      setChip($("#chip-gateway"), "off", "No model", "Verdicts came from recorded evidence or heuristics");
    }
    verdictEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function reset() {
    state.report = null;
    state.sampleId = null;
    answerEl.value = "";
    updateCount();
    setActiveSample(null);
    setHint(defaultHint(), null);
    claimsSection.hidden = true;
    claimsEl.innerHTML = "";
    verdictEl.setAttribute("aria-busy", "false");
    verdictEl.innerHTML = `
      <div class="empty-verdict">
        <div class="empty-mark" aria-hidden="true">
          <svg viewBox="0 0 120 120">
            <circle cx="60" cy="60" r="47" fill="none" stroke="currentColor" stroke-width="1.5" stroke-dasharray="4 7"/>
            <path d="M60 33v27l17 10" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>
            <circle cx="60" cy="60" r="4" fill="currentColor"/>
          </svg>
        </div>
        <h2>No answer checked yet</h2>
        <p>The verdict, the expiry date, and every source will appear here.</p>
      </div>`;
  }

  /* --------------------------------------------------------------- check */

  async function check() {
    if (state.busy) return;
    const text = answerEl.value.trim();
    if (text.length < 12) {
      toast("Add at least a sentence to check.");
      answerEl.focus();
      return;
    }

    state.busy = true;
    checkBtn.disabled = true;
    checkBtn.querySelector(".btn-label").textContent = "Checking…";
    renderLoading();

    try {
      const response = await fetch("/api/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, sample_id: state.sampleId }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      window.clearInterval(renderLoading._timer);
      renderReport(payload.report);
    } catch (error) {
      window.clearInterval(renderLoading._timer);
      renderError(error instanceof Error ? error.message : String(error));
      toast("Check failed. See the panel.");
    } finally {
      state.busy = false;
      checkBtn.disabled = false;
      checkBtn.querySelector(".btn-label").textContent = "Check freshness";
    }
  }

  /* -------------------------------------------------------------- actions */

  async function copyReport() {
    if (!state.report) return;
    const json = JSON.stringify(state.report, null, 2);
    try {
      await navigator.clipboard.writeText(json);
      toast("Report copied as JSON.");
    } catch {
      downloadReport();
    }
  }

  function downloadReport() {
    if (!state.report) return;
    const blob = new Blob([JSON.stringify(state.report, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `best-before-${state.report.id}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    toast("Report downloaded.");
  }

  /* ----------------------------------------------------------------- boot */

  async function loadHealth() {
    try {
      const response = await fetch("/api/health");
      const health = await response.json();
      state.modes = health.modes;
      if (health.modes.tavily) {
        setChip($("#chip-evidence"), "live", "Tavily live", "Live web search is enabled");
      } else {
        setChip($("#chip-evidence"), "recorded", "Recorded evidence",
          "No TAVILY_API_KEY: sample answers use evidence recorded from the cited pages");
      }
      if (health.modes.bifrost) {
        setChip($("#chip-gateway"), "live", "Bifrost gateway", `Gateway reachable at ${health.gateway.url}`);
      } else if (health.modes.direct) {
        setChip($("#chip-gateway"), "recorded", "Direct · Groq",
          "Bifrost is not reachable, so model calls go straight to Groq");
      } else {
        setChip($("#chip-gateway"), "off", "No model", "No gateway and no provider key");
      }
    } catch {
      setChip($("#chip-evidence"), "off", "Offline", "Health check failed");
      setChip($("#chip-gateway"), "off", "Offline", "Health check failed");
    }
  }

  async function loadSamples() {
    try {
      const response = await fetch("/api/samples");
      const payload = await response.json();
      state.samples = payload.samples || [];
      renderSamples();
    } catch {
      state.samples = [];
    }
  }

  samplesEl.addEventListener("click", (event) => {
    const button = event.target.closest(".sample");
    if (!button || state.busy) return;
    selectSample(button.dataset.id);
    check();
  });

  answerEl.addEventListener("input", () => {
    updateCount();
    if (!state.sampleId) return;
    const sample = state.samples.find((row) => row.id === state.sampleId);
    if (sample && normalize(answerEl.value) !== normalize(sample.answer)) {
      state.sampleId = null;
      setActiveSample(null);
      setHint(
        state.modes.tavily
          ? "Edited — this will be checked as new text against live search."
          : "Edited — no Tavily key, so this text can only come back unverified.",
        "edited",
      );
    }
  });

  checkBtn.addEventListener("click", check);
  clearBtn.addEventListener("click", reset);

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      check();
    }
  });

  updateCount();

  (async () => {
    await Promise.all([loadHealth(), loadSamples()]);
    setHint(defaultHint(), null);
    if (state.samples.length) {
      selectSample(state.samples[0].id);
      check();
    }
  })();
})();
