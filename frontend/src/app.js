/**
 * Quorum Client Application
 * Modern Vanilla ES6+ Event-Driven Voting Dashboard
 * Enforces one-vote-per-UID and conceals voting statistics until a ballot is cast.
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
   * Fetch all active polls with voter fingerprint to check vote status
   */
  async function fetchPolls() {
    try {
      const url = `${API_BASE}/api/polls?voter_fingerprint=${encodeURIComponent(state.voterId)}`;
      const res = await fetch(url);
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
      const url = `${API_BASE}/api/polls/${state.activePollId}?voter_fingerprint=${encodeURIComponent(state.voterId)}`;
      const res = await fetch(url);
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

    const currentPoll = state.polls.find(p => p.id === state.activePollId);
    if (currentPoll && currentPoll.has_voted) {
      showToast("You have already voted on this poll.", "error");
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
        showToast("Vote Accepted! Results unlocked.", "success");
        // Immediately fetch updated poll to reveal statistics
        setTimeout(refreshActivePollTallies, 200);
      } else if (response.status === 409) {
        const errorData = await response.json().catch(() => ({}));
        showToast(errorData.detail || "You have already cast a vote on this poll.", "error");
        setTimeout(refreshActivePollTallies, 200);
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
    const poll = state.polls.find(p => p.id === pollId);
    if (poll) {
      state.selectedOptionId = poll.has_voted ? poll.user_voted_option_id : null;
      renderActivePoll(poll, true);
      renderPollSidebar();
    }
  }

  function renderPollSidebar() {
    if (!elements.pollListContainer) return;
    elements.activePollCount.textContent = `${state.polls.length} Available`;

    elements.pollListContainer.innerHTML = state.polls
      .map(poll => {
        const isActive = poll.id === state.activePollId;
        const badgeMarkup = poll.has_voted
          ? `<span class="vote-badge poll-badge-voted">✓ Voted • ${poll.total_votes !== null ? poll.total_votes : ''} votes</span>`
          : `<span class="vote-badge">${poll.options.length} options</span>`;

        return `
          <li class="poll-item-card ${isActive ? 'active' : ''}" data-poll-id="${poll.id}">
            <div class="poll-item-title">${escapeHtml(poll.title)}</div>
            <div class="poll-item-meta">
              <span>${poll.has_voted ? 'Results unlocked' : 'Vote to reveal'}</span>
              ${badgeMarkup}
            </div>
          </li>
        `;
      })
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

    // Total votes: hidden before voting
    if (poll.has_voted && poll.total_votes !== null) {
      elements.totalVoteCount.textContent = poll.total_votes.toLocaleString();
    } else {
      elements.totalVoteCount.textContent = "Hidden until voted";
    }

    const hasVoted = poll.has_voted;
    const votedOptionId = poll.user_voted_option_id;

    if (fullRebuild) {
      elements.optionsContainer.innerHTML = poll.options
        .map(opt => {
          const isSelected = hasVoted ? (opt.id === votedOptionId) : (opt.id === state.selectedOptionId);
          const isUserChoice = hasVoted && (opt.id === votedOptionId);

          let statsMarkup = "";
          let progressWidth = "0%";

          if (hasVoted && opt.vote_count !== null && opt.percentage !== null) {
            progressWidth = `${opt.percentage}%`;
            statsMarkup = `
              <div class="option-stats">
                <span class="option-percentage">${opt.percentage}%</span>
                <span class="option-votes">${opt.vote_count.toLocaleString()} votes</span>
              </div>
            `;
          } else {
            // Conceal statistics prior to voting
            statsMarkup = `
              <div class="option-stats hidden-stats">
                <span class="stats-hidden-badge">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/>
                  </svg>
                  Vote to unlock
                </span>
              </div>
            `;
          }

          const cardClasses = [
            "option-card",
            isSelected ? "selected" : "",
            isUserChoice ? "user-choice" : "",
            hasVoted ? "locked-selection" : "",
          ].filter(Boolean).join(" ");

          const userTag = isUserChoice ? `<span class="user-vote-tag">Your Vote</span>` : "";

          return `
            <div class="${cardClasses}" data-option-id="${opt.id}">
              <div class="option-progress-bg" style="width: ${progressWidth};"></div>
              <div class="option-content">
                <div class="option-left">
                  <div class="radio-indicator">
                    <div class="radio-dot"></div>
                  </div>
                  <span class="option-label">${escapeHtml(opt.label)}</span>
                  ${userTag}
                </div>
                ${statsMarkup}
              </div>
            </div>
          `;
        })
        .join("");

      // Attach click events only if user has not yet voted
      if (!hasVoted) {
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
      }
    } else {
      // Smoothly update tallies if already voted, without rebuilding DOM elements
      if (hasVoted) {
        poll.options.forEach(opt => {
          const card = elements.optionsContainer.querySelector(`[data-option-id="${opt.id}"]`);
          if (card) {
            const progressBg = card.querySelector(".option-progress-bg");
            const percentageSpan = card.querySelector(".option-percentage");
            const votesSpan = card.querySelector(".option-votes");

            if (progressBg && opt.percentage !== null) progressBg.style.width = `${opt.percentage}%`;
            if (percentageSpan && opt.percentage !== null) percentageSpan.textContent = `${opt.percentage}%`;
            if (votesSpan && opt.vote_count !== null) votesSpan.textContent = `${opt.vote_count.toLocaleString()} votes`;
          }
        });
      }
    }

    updateVoteButtonState();
  }

  function updateVoteButtonState() {
    if (!elements.btnSubmitVote) return;

    const currentPoll = state.polls.find(p => p.id === state.activePollId);
    const hasVoted = currentPoll && currentPoll.has_voted;

    if (hasVoted) {
      // Voted state: show confirmed badge
      elements.btnSubmitVote.style.display = "none";
      let confirmedBadge = document.getElementById("vote-confirmed-badge");
      if (!confirmedBadge) {
        confirmedBadge = document.createElement("div");
        confirmedBadge.id = "vote-confirmed-badge";
        confirmedBadge.className = "vote-confirmed-badge";
        confirmedBadge.innerHTML = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="#10b981">
            <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
          </svg>
          Vote Recorded
        `;
        elements.btnSubmitVote.parentElement.appendChild(confirmedBadge);
      }
      confirmedBadge.style.display = "inline-flex";
    } else {
      // Unvoted state: show submit button
      const confirmedBadge = document.getElementById("vote-confirmed-badge");
      if (confirmedBadge) confirmedBadge.style.display = "none";

      elements.btnSubmitVote.style.display = "inline-flex";
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
