"""Self-contained MCP App shown while a Boniscore report is generated."""

BONISCORE_PROGRESS_UI_URI = "ui://boniforce/boniscore-progress.html"

BONISCORE_PROGRESS_HTML = r"""
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Boniscore wird erstellt</title>
  <style>
    :root {
      color-scheme: light dark;
      --ink: #102039;
      --muted: #64748b;
      --paper: #f7f9fc;
      --panel: rgba(255, 255, 255, 0.9);
      --line: rgba(15, 35, 64, 0.12);
      --blue: #2864dc;
      --blue-soft: #dce8ff;
      --green: #15805b;
      --amber: #b56708;
      --red: #bf3b46;
      --shadow: 0 16px 40px rgba(24, 48, 86, 0.12);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      padding: 8px;
      background: transparent;
      color: var(--ink);
      font-family: "Avenir Next", Avenir, "Segoe UI", sans-serif;
    }

    .card {
      position: relative;
      width: 100%;
      min-height: 226px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 22px;
      background:
        radial-gradient(circle at 92% 5%, rgba(40, 100, 220, 0.16), transparent 34%),
        linear-gradient(145deg, var(--panel), var(--paper));
      box-shadow: var(--shadow);
      padding: 22px;
    }

    .card::before {
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 4px;
      background: var(--blue);
    }

    .eyebrow {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 22px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.13em;
      text-transform: uppercase;
    }

    .brand { color: var(--blue); }

    .live {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      letter-spacing: 0.08em;
    }

    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--blue);
      box-shadow: 0 0 0 0 rgba(40, 100, 220, 0.35);
      animation: pulse 1.8s ease-out infinite;
    }

    h1 {
      margin: 0 0 7px;
      max-width: 560px;
      font-family: Georgia, "Times New Roman", serif;
      font-size: clamp(25px, 5.5vw, 35px);
      font-weight: 600;
      line-height: 1.08;
      letter-spacing: -0.025em;
    }

    .message {
      min-height: 22px;
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }

    .progress-shell {
      margin-top: 24px;
      height: 7px;
      overflow: hidden;
      border-radius: 999px;
      background: var(--blue-soft);
    }

    .progress {
      width: 8%;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, #255bd2, #5d91f2);
      transition: width 700ms cubic-bezier(.22, .9, .3, 1), background 300ms ease;
    }

    .footer {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      margin-top: 12px;
      color: var(--muted);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }

    .result {
      display: none;
      grid-template-columns: minmax(112px, .8fr) minmax(160px, 1.3fr);
      gap: 18px;
      margin-top: 20px;
    }

    .score-box, .decision-box {
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255,255,255,.58);
      padding: 15px 16px;
    }

    .label {
      color: var(--muted);
      font-size: 10px;
      font-weight: 800;
      letter-spacing: .11em;
      text-transform: uppercase;
    }

    .score-row {
      display: flex;
      align-items: baseline;
      gap: 6px;
      margin-top: 5px;
    }

    .score {
      font-family: Georgia, "Times New Roman", serif;
      font-size: 44px;
      line-height: 1;
    }

    .out-of { color: var(--muted); font-size: 13px; }

    .decision {
      margin-top: 7px;
      font-size: 18px;
      font-weight: 700;
    }

    .limit {
      margin-top: 5px;
      color: var(--muted);
      font-size: 13px;
    }

    .open-chat {
      display: none;
      width: 100%;
      margin-top: 14px;
      border: 0;
      border-radius: 12px;
      background: var(--ink);
      color: #fff;
      cursor: pointer;
      padding: 11px 14px;
      font: inherit;
      font-size: 13px;
      font-weight: 700;
    }

    .open-chat:hover { opacity: .9; }
    .open-chat:focus-visible { outline: 3px solid rgba(40,100,220,.35); outline-offset: 2px; }

    .card.complete::before { background: var(--green); }
    .card.complete .dot { background: var(--green); animation: none; box-shadow: none; }
    .card.complete .progress { background: var(--green); }
    .card.complete .progress-shell, .card.failed .progress-shell { display: none; }
    .card.complete .result { display: grid; }
    .card.failed::before { background: var(--red); }
    .card.failed .dot { background: var(--red); animation: none; box-shadow: none; }

    @keyframes pulse {
      70% { box-shadow: 0 0 0 8px rgba(40, 100, 220, 0); }
      100% { box-shadow: 0 0 0 0 rgba(40, 100, 220, 0); }
    }

    @media (prefers-color-scheme: dark) {
      :root {
        --ink: #edf4ff;
        --muted: #a8b5c8;
        --paper: #0d1727;
        --panel: rgba(17, 30, 50, .94);
        --line: rgba(189, 211, 244, .15);
        --blue-soft: #1d355d;
        --shadow: 0 16px 40px rgba(0, 0, 0, .24);
      }
      .score-box, .decision-box { background: rgba(255,255,255,.035); }
      .open-chat { background: #edf4ff; color: #102039; }
    }

    @media (max-width: 430px) {
      .card { padding: 19px; }
      .result { grid-template-columns: 1fr; gap: 10px; }
    }

    @media (prefers-reduced-motion: reduce) {
      .dot { animation: none; }
      .progress { transition: none; }
    }
  </style>
</head>
<body>
  <main class="card" id="card" aria-live="polite">
    <div class="eyebrow">
      <span class="brand">Boniforce · Kreditprüfung</span>
      <span class="live"><span class="dot" aria-hidden="true"></span><span id="state">Live</span></span>
    </div>

    <h1 id="title">Boniscore-Bericht gestartet</h1>
    <p class="message" id="message">Die Unternehmensdaten werden geprüft.</p>

    <div class="progress-shell" role="progressbar" aria-label="Fortschritt" aria-valuemin="0" aria-valuemax="100" aria-valuenow="8" id="progressShell">
      <div class="progress" id="progress"></div>
    </div>
    <div class="footer" id="footer">
      <span id="phase">Daten werden vorbereitet</span>
      <span id="elapsed">00:00</span>
    </div>

    <section class="result" aria-label="Boniscore-Ergebnis">
      <div class="score-box">
        <div class="label">Boniscore</div>
        <div class="score-row"><span class="score" id="score">—</span><span class="out-of">/ 100</span></div>
      </div>
      <div class="decision-box">
        <div class="label">Einschätzung</div>
        <div class="decision" id="decision">Bericht abgeschlossen</div>
        <div class="limit" id="limit"></div>
      </div>
    </section>
    <button class="open-chat" id="openChat" type="button">Auswertung im Chat öffnen</button>
  </main>

  <script>
    (() => {
      "use strict";

      const card = document.getElementById("card");
      const stateEl = document.getElementById("state");
      const titleEl = document.getElementById("title");
      const messageEl = document.getElementById("message");
      const phaseEl = document.getElementById("phase");
      const elapsedEl = document.getElementById("elapsed");
      const progressEl = document.getElementById("progress");
      const progressShell = document.getElementById("progressShell");
      const scoreEl = document.getElementById("score");
      const decisionEl = document.getElementById("decision");
      const limitEl = document.getElementById("limit");
      const openChat = document.getElementById("openChat");

      const pendingRequests = new Map();
      let nextRequestId = 1;
      let startedAt = Date.now();
      let jobId = null;
      let reportId = null;
      let lastStatus = "queued";
      let stopped = false;
      let fetchingReport = false;
      let pollTimer = null;

      function bridgeRequest(method, params) {
        const id = nextRequestId++;
        window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
        return new Promise((resolve, reject) => {
          pendingRequests.set(id, { resolve, reject });
          window.setTimeout(() => {
            if (!pendingRequests.has(id)) return;
            pendingRequests.delete(id);
            reject(new Error("Bridge request timed out"));
          }, 15000);
        });
      }

      async function callTool(name, args) {
        try {
          return await bridgeRequest("tools/call", { name, arguments: args });
        } catch (error) {
          if (window.openai && typeof window.openai.callTool === "function") {
            return window.openai.callTool(name, args);
          }
          throw error;
        }
      }

      function unwrap(result) {
        if (!result || typeof result !== "object") return {};
        if (result.structuredContent && typeof result.structuredContent === "object") {
          return result.structuredContent;
        }
        return result;
      }

      function setProgress(value) {
        const bounded = Math.max(0, Math.min(100, Math.round(value)));
        progressEl.style.width = `${bounded}%`;
        progressShell.setAttribute("aria-valuenow", String(bounded));
      }

      function formatElapsed() {
        const total = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
        const minutes = String(Math.floor(total / 60)).padStart(2, "0");
        const seconds = String(total % 60).padStart(2, "0");
        return `${minutes}:${seconds}`;
      }

      function tick() {
        if (stopped) return;
        elapsedEl.textContent = formatElapsed();
        const seconds = (Date.now() - startedAt) / 1000;
        if (["queued", "pending"].includes(lastStatus)) setProgress(Math.min(28, 10 + seconds * .35));
        else setProgress(Math.min(91, 30 + seconds * .55));
      }

      function statusCopy(status) {
        if (["queued", "pending"].includes(status)) {
          return ["In Warteschlange", "Der Bericht ist eingeplant und startet in Kürze.", "Auf Verarbeitung warten"];
        }
        return ["Berechnung läuft", "Register-, Finanz- und Risikodaten werden ausgewertet.", "Boniscore wird berechnet"];
      }

      function renderStatus(payload) {
        const data = unwrap(payload);
        jobId = data.job_id || jobId;
        reportId = data.report_id || reportId;
        const nestedStatus = data.final_status && data.final_status.status;
        const status = String(data.status || nestedStatus || lastStatus || "queued").toLowerCase();
        lastStatus = status;

        if (data.report) {
          renderReport(data.report);
          return;
        }
        if (data.done && ["completed", "finished"].includes(status) && reportId) {
          fetchReport();
          return;
        }
        if (["failed", "error", "cancelled", "canceled"].includes(status)) {
          fail("Die Berechnung konnte nicht abgeschlossen werden. Die Details erscheinen im Chat.");
          return;
        }

        const copy = statusCopy(status);
        stateEl.textContent = copy[0];
        messageEl.textContent = copy[1];
        phaseEl.textContent = copy[2];
      }

      function decisionText(report) {
        const label = report.score_details && report.score_details.label;
        if (label) return String(label);
        const result = String(report.credit_assessment_result || "").toUpperCase();
        return ({ APPROVE: "Freigabe empfohlen", REVIEW: "Manuelle Prüfung empfohlen", DECLINE: "Ablehnung empfohlen" })[result] || "Bericht abgeschlossen";
      }

      function formatLimit(value) {
        if (value === null || value === undefined || value === "") return "";
        const amount = Number(value);
        if (!Number.isFinite(amount)) return `Kreditlimit: ${String(value)}`;
        return `Kreditlimit: ${new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(amount)}`;
      }

      function renderReport(payload) {
        const report = unwrap(payload);
        stopped = true;
        if (pollTimer) window.clearTimeout(pollTimer);
        card.classList.add("complete");
        stateEl.textContent = "Fertig";
        titleEl.textContent = "Boniscore liegt vor";
        messageEl.textContent = "Die Bonitätsprüfung wurde erfolgreich abgeschlossen.";
        phaseEl.textContent = "Ergebnis bereit";
        elapsedEl.textContent = formatElapsed();
        setProgress(100);
        scoreEl.textContent = report.score === null || report.score === undefined ? "—" : String(report.score);
        decisionEl.textContent = decisionText(report);
        limitEl.textContent = formatLimit(report.credit_limit);
        if (window.openai && typeof window.openai.sendFollowUpMessage === "function" && reportId) {
          openChat.style.display = "block";
        }
      }

      function fail(message) {
        stopped = true;
        if (pollTimer) window.clearTimeout(pollTimer);
        card.classList.add("failed");
        stateEl.textContent = "Fehler";
        titleEl.textContent = "Bericht nicht abgeschlossen";
        messageEl.textContent = message;
        phaseEl.textContent = "Bitte Chat-Antwort prüfen";
        elapsedEl.textContent = formatElapsed();
      }

      async function fetchReport() {
        if (!reportId || stopped || fetchingReport) return;
        fetchingReport = true;
        stateEl.textContent = "Ergebnis wird geladen";
        phaseEl.textContent = "Bericht abrufen";
        try {
          const result = await callTool("get_report", { report_id: reportId });
          renderReport(result);
        } catch (error) {
          fail("Der fertige Bericht konnte in der Karte nicht geladen werden. Die Chat-Antwort bleibt verfügbar.");
        }
      }

      async function poll() {
        if (stopped || !jobId) return;
        try {
          const result = await callTool("get_job_status", { job_id: jobId, wait_seconds: 0 });
          renderStatus(result);
        } catch (error) {
          messageEl.textContent = "Die Live-Anzeige verbindet sich erneut. Die Berechnung läuft weiter.";
        }
        if (!stopped) pollTimer = window.setTimeout(poll, 3000);
      }

      function acceptInitial(payload) {
        const data = unwrap(payload);
        if (!data || (!data.job_id && !data.report_id && !data.report)) return;
        renderStatus(data);
        if (!stopped && jobId && !pollTimer) pollTimer = window.setTimeout(poll, 500);
      }

      window.addEventListener("message", (event) => {
        if (event.source !== window.parent) return;
        const message = event.data;
        if (!message || message.jsonrpc !== "2.0") return;

        if (message.id !== undefined && pendingRequests.has(message.id)) {
          const pending = pendingRequests.get(message.id);
          pendingRequests.delete(message.id);
          if (message.error) pending.reject(message.error);
          else pending.resolve(message.result);
          return;
        }

        if (message.method === "ui/notifications/tool-result") {
          acceptInitial(message.params);
        }
      }, { passive: true });

      openChat.addEventListener("click", () => {
        if (!reportId || !window.openai || typeof window.openai.sendFollowUpMessage !== "function") return;
        window.openai.sendFollowUpMessage({
          prompt: `Bitte erläutere den Boniscore-Bericht ${reportId} und gib eine kurze Kreditentscheidung.`,
          scrollToBottom: true
        });
      });

      window.setInterval(tick, 1000);
      tick();
      if (window.openai && window.openai.toolOutput) acceptInitial(window.openai.toolOutput);
    })();
  </script>
</body>
</html>
""".strip()
