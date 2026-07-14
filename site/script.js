"use strict";

function copyInstallCommand(button) {
  const command = "codex plugin marketplace add 90le/worker-rights-cn --ref main";
  const originalLabel = button.textContent;

  function showStatus(label) {
    button.textContent = label;
    window.setTimeout(function restoreLabel() {
      button.textContent = originalLabel;
    }, 1800);
  }

  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(command).then(
      function copied() { showStatus("已复制"); },
      function copyFailed() { showStatus("请手动复制"); }
    );
    return;
  }

  const selection = window.getSelection();
  const code = button.parentElement.querySelector("code");
  if (selection && code) {
    const range = document.createRange();
    range.selectNodeContents(code);
    selection.removeAllRanges();
    selection.addRange(range);
  }
  showStatus("已选中命令");
}
