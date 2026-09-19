/**
 * Quorum Client Application
 * Modern Vanilla ES6+ Event-Driven Voting Dashboard
 */

(() => {
  // Configuration
  const API_BASE = window.location.port === "80" || window.location.port === "" 
    ? "" 
    : (window.location.hostname === "localhost" && window.location.port !== "8000" ? "http://localhost:8000" : "");
  
  const POLL_INTERVAL_MS = 3000;
  const HEALTH_CHECK_INTERVAL_MS = 5000;

  // Application State
  const state = {
    voterId: "",
    polls: [],
    activePollId: null,
    selectedOptionId: null,
    isSubmitting: false,
    pollTimer: null,
    healthTimer: null,
    isClusterReady: false,
  };

  // DOM Elements
  const elements = {
    voterFingerprint: document.getElementById("voter-fingerprint"),
    clusterStatusBadge: document.getElementById("cluster-status-badge"),
    clusterStatusDot: document.getElementById("cluster-status-dot"),
    clusterStatusText: document.getElementById("cluster-status-text"),
    alertBanner: document.getElementById("alert-banner"),
    alertBannerText: document.getElementById("alert-banner-text"),
    pollListContainer: document.getElementById("poll-list-container"),
    activePollCount: document.getElementById("active-poll-count"),
    activePollTitle: document.getElementById("active-poll-title"),
    activePollDesc: document.getElementById("active-poll-desc"),
    optionsContainer: document.getElementById("options-container"),
    totalVoteCount: document.getElementById("total-vote-count"),
    btnSubmitVote: document.getElementById("btn-submit-vote"),
    btnVoteText: document.getElementById("btn-vote-text"),
    voteSpinner: document.getElementById("vote-spinner"),
    toastContainer: document.getElementById("toast-container"),
  };

  // --- Utility Functions ---

  /**
   * Generate or retrieve client UUID fingerprint from localStorage
   */
  function initVoterFingerprint() {
    let voterId = localStorage.getItem("quorum_voter_uuid");
    if (!voterId) {
      if (window.crypto && window.crypto.randomUUID) {
        voterId = window.crypto.randomUUID();
      } else {
        voterId = "voter-" + Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
      }
      localStorage.setItem("quorum_voter_uuid", voterId);
    }
    state.voterId = voterId;
    if (elements.voterFingerprint) {
      elements.voterFingerprint.textContent = voterId.substring(0, 8) + "...";
      elements.voterFingerprint.parentElement.title = `Voter ID: ${voterId}`;
    }
  }

  /**
   * Display a floating toast notification
   */
  function showToast(message, type = "success", durationMs = 3500) {
    if (!elements.toastContainer) return;

    const toast = document.createElement("div");
    toast.className = `toast ${type === "error" ? "toast-error" : type === "info" ? "toast-info" : ""}`;
    
    const iconSvg = type === "error" 
      ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="#f43f5e"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>`
      : `<svg width="20" height="20" viewBox="0 0 24 24" fill="#10b981"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>`;

    toast.innerHTML = `
      ${iconSvg}
      <span>${escapeHtml(message)}</span>
    `;

    elements.toastContainer.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateY(10px)";
      setTimeout(() => toast.remove(), 300);
    }, durationMs);
  }

  /**
   * Sanitize text against XSS injection
   */
  function escapeHtml(str) {
    if (!str) return "";
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  // --- API Client Interactions ---

  /**
   * Check /readyz endpoint to verify downstream cluster availability
   */
  async function checkClusterReadiness() {
    try {
      const response = await fetch(`${API_BASE}/readyz`);
      const data = await response.json();

      if (response.ok && data.status === "ready") {
        state.isClusterReady = true;
        elements.clusterStatusDot.classList.remove("unready");
        elements.clusterStatusText.textContent = "Cluster Online";
        elements.alertBanner.classList.remove("visible");
      } else {
        state.isClusterReady = false;
        elements.clusterStatusDot.classList.add("unready");
        elements.clusterStatusText.textContent = "Degraded";
        elements.alertBannerText.textContent = `Backend degraded: DB=${data.database || 'err'}, Redis=${data.redis || 'err'}`;
        elements.alertBanner.classList.add("visible");
      }
    } catch (err) {
      state.isClusterReady = false;
      elements.clusterStatusDot.classList.add("unready");
      elements.clusterStatusText.textContent = "Disconnected";
      elements.alertBannerText.textContent = "Unable to connect to Quorum API Gateway. Retrying in background...";
      elements.alertBanner.classList.add("visible");
    }
  }

  /**
   * Fetch all active polls
   */
  async function fetchPolls() {
    try {
      const res = await fetch(`${API_BASE}/api/polls`);
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      const polls = await res.json();
      state.polls = polls;

      renderPollSidebar();

      // If active poll not set, select first poll
      if (polls.length > 0) {
        if (!state.activePollId || !polls.some(p => p.id === state.activePollId)) {
          selectPoll(polls[0].id);
        } else {
          // Refresh active poll details
          const active = polls.find(p => p.id === state.activePollId);
          renderActivePoll(active);
        }
      }
    } catch (err) {
      console.warn("Failed to fetch polls:", err);
    }
  }

  /**
   * Fetch latest tallies for active poll
   */
  async function refreshActivePollTallies() {
    if (!state.activePollId) return;
    try {
      const res = await fetch(`${API_BASE}/api/polls/${state.activePollId}`);
      if (!res.ok) return;
      const poll = await res.json();
      
      // Update poll in state array
      const idx = state.polls.findIndex(p => p.id === poll.id);
      if (idx !== -1) state.polls[idx] = poll;

      renderActivePoll(poll, false);
      renderPollSidebar();
    } catch (err) {
      console.warn("Failed refreshing poll tally:", err);
    }
  }

  /**
   * Submit selected vote to API Gateway
   */
  async function submitVote() {
    if (!state.activePollId || !state.selectedOptionId || state.isSubmitting) {
      return;
    }

    state.isSubmitting = true;
    updateVoteButtonState();

    try {
      const response = await fetch(`${API_BASE}/api/polls/${state.activePollId}/vote`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          option_id: state.selectedOptionId,
          voter_fingerprint: state.voterId,
        }),
      });

      if (response.status === 202) {
        showToast("Vote Accepted! Ingested into Redis stream for batch persistence.", "success");
        // Trigger rapid refresh to pick up Redis tally offset
        setTimeout(refreshActivePollTallies, 250);
      } else {
        const errorData = await response.json().catch(() => ({}));
        showToast(errorData.detail || "Failed to submit vote.", "error");
      }
    } catch (err) {
      showToast("Network error: Could not reach API gateway.", "error");
    } finally {
      state.isSubmitting = false;
      updateVoteButtonState();
    }
  }

  // --- Rendering UI ---

  function selectPoll(pollId) {
    state.activePollId = pollId;
    state.selectedOptionId = null;
    const poll = state.polls.find(p => p.id === pollId);
    if (poll) {
      renderActivePoll(poll, true);
      renderPollSidebar();
    }
  }

  function renderPollSidebar() {
    if (!elements.pollListContainer) return;
    elements.activePollCount.textContent = `${state.polls.length} Available`;

    elements.pollListContainer.innerHTML = state.polls
      .map(poll => `
        <li class="poll-item-card ${poll.id === state.activePollId ? 'active' : ''}" data-poll-id="${poll.id}">
          <div class="poll-item-title">${escapeHtml(poll.title)}</div>
          <div class="poll-item-meta">
            <span>${poll.options.length} choices</span>
            <span class="vote-badge">${poll.total_votes} votes</span>
          </div>
        </li>
      `)
      .join("");

    // Attach click handlers
    elements.pollListContainer.querySelectorAll(".poll-item-card").forEach(el => {
      el.addEventListener("click", () => {
        const pollId = parseInt(el.getAttribute("data-poll-id"), 10);
        selectPoll(pollId);
      });
    });
  }

  function renderActivePoll(poll, fullRebuild = true) {
    if (!poll) return;

    elements.activePollTitle.textContent = poll.title;
    elements.activePollDesc.textContent = poll.description || "Cast your vote below.";
    elements.totalVoteCount.textContent = poll.total_votes.toLocaleString();

    if (fullRebuild) {
      elements.optionsContainer.innerHTML = poll.options
        .map(opt => `
          <div class="option-card ${opt.id === state.selectedOptionId ? 'selected' : ''}" data-option-id="${opt.id}">
            <div class="option-progress-bg" style="width: ${opt.percentage}%;"></div>
            <div class="option-content">
              <div class="option-left">
                <div class="radio-indicator">
                  <div class="radio-dot"></div>
                </div>
                <span class="option-label">${escapeHtml(opt.label)}</span>
              </div>
              <div class="option-stats">
                <span class="option-percentage">${opt.percentage}%</span>
                <span class="option-votes">${opt.vote_count.toLocaleString()} votes</span>
              </div>
            </div>
          </div>
        `)
        .join("");

      // Attach click events
      elements.optionsContainer.querySelectorAll(".option-card").forEach(card => {
        card.addEventListener("click", () => {
          const optId = parseInt(card.getAttribute("data-option-id"), 10);
          state.selectedOptionId = optId;

          // Update selection visual
          elements.optionsContainer.querySelectorAll(".option-card").forEach(c => {
            c.classList.toggle("selected", parseInt(c.getAttribute("data-option-id"), 10) === optId);
          });

          updateVoteButtonState();
        });
      });
    } else {
      // Smoothly update tallies without rebuilding DOM elements
      poll.options.forEach(opt => {
        const card = elements.optionsContainer.querySelector(`[data-option-id="${opt.id}"]`);
        if (card) {
          const progressBg = card.querySelector(".option-progress-bg");
          const percentageSpan = card.querySelector(".option-percentage");
          const votesSpan = card.querySelector(".option-votes");

          if (progressBg) progressBg.style.width = `${opt.percentage}%`;
          if (percentageSpan) percentageSpan.textContent = `${opt.percentage}%`;
          if (votesSpan) votesSpan.textContent = `${opt.vote_count.toLocaleString()} votes`;
        }
      });
    }

    updateVoteButtonState();
  }

  function updateVoteButtonState() {
    if (!elements.btnSubmitVote) return;

    if (state.isSubmitting) {
      elements.btnSubmitVote.disabled = true;
      elements.btnVoteText.textContent = "Ingesting...";
      elements.voteSpinner.style.display = "inline-block";
    } else {
      elements.btnSubmitVote.disabled = !state.selectedOptionId;
      elements.btnVoteText.textContent = "Submit Vote";
      elements.voteSpinner.style.display = "none";
    }
  }

  // --- App Initialization ---

  function init() {
    initVoterFingerprint();

    // Event listener for vote button
    if (elements.btnSubmitVote) {
      elements.btnSubmitVote.addEventListener("click", submitVote);
    }

    // Initial readiness probe and poll load
    checkClusterReadiness();
    fetchPolls();

    // Recurring polling loops
    state.healthTimer = setInterval(checkClusterReadiness, HEALTH_CHECK_INTERVAL_MS);
    state.pollTimer = setInterval(refreshActivePollTallies, POLL_INTERVAL_MS);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
