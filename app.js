"use strict";

// Examples are deliberately local and illustrative; this page does not read agent sessions.
const evidence = {
  split:
    "L42  Edit auth/login.py\nL45  Edit auth/session.py\nL48  Edit auth/token.py",
  tests: "L85  Bash: pytest -q tests/auth\nL86  24 passed in 1.82s",
  retry:
    "L91  Agent: The retry path needs a regression test.\nL93  Agent: I have not added that test yet.",
};

function setupTabs(list, onChange) {
  const tabs = Array.from(list.querySelectorAll('[role="tab"]'));
  function select(tab, focus = false) {
    tabs.forEach((item) => {
      const selected = item === tab;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
    });
    onChange(tab);
    if (focus) tab.focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (event) => {
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft")
        next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next !== undefined) {
        event.preventDefault();
        select(tabs[next], true);
      }
    });
  });
}

const evidencePanel = document.getElementById("example-evidence");
const citationButtons = Array.from(document.querySelectorAll(".citation"));
setupTabs(document.querySelector(".demo-tabs"), (tab) => {
  document.querySelectorAll(".terminal-panel").forEach((panel) => {
    panel.hidden = panel.id !== tab.getAttribute("aria-controls");
  });
  evidencePanel.hidden = true;
  citationButtons.forEach((button) =>
    button.setAttribute("aria-expanded", "false"),
  );
});

citationButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const shouldOpen = button.getAttribute("aria-expanded") !== "true";
    citationButtons.forEach((citation) =>
      citation.setAttribute("aria-expanded", "false"),
    );
    button.setAttribute("aria-expanded", String(shouldOpen));
    evidencePanel.hidden = !shouldOpen;
    document.getElementById("evidence-text").textContent =
      evidence[button.dataset.evidence];
  });
});

const copyStatus = document.getElementById("copy-status");

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const command = document.getElementById(button.dataset.copyTarget);
    const value = command.textContent;
    try {
      await navigator.clipboard.writeText(value);
      copyStatus.textContent = "Commands copied.";
    } catch {
      // Local file previews and denied clipboard access still allow manual copying.
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(command);
      selection.removeAllRanges();
      selection.addRange(range);
      copyStatus.textContent = "Command selected. Press ⌘C or Ctrl+C to copy.";
    }
  });
});

const recording = document.getElementById("demo");
const recordingImage = recording.querySelector("img");
function syncRecording() {
  if (recording.open) {
    if (!recordingImage.hasAttribute("src"))
      recordingImage.src = recordingImage.dataset.src;
  } else recordingImage.removeAttribute("src");
}
recording.addEventListener("toggle", syncRecording);
syncRecording();
document.getElementById("pause-recording").addEventListener("click", () => {
  recording.open = false;
  recording.querySelector("summary").focus();
});

// The recording starts expanded; visitors can stop it or reopen it later.
function openRecordingFromHash() {
  if (window.location.hash === "#demo") recording.open = true;
}
window.addEventListener("hashchange", openRecordingFromHash);
openRecordingFromHash();
