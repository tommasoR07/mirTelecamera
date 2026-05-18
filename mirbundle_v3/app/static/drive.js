(() => {
  const resultEl = document.getElementById('driveResult');
  const linearInput = document.getElementById('linearSpeed');
  const angularInput = document.getElementById('angularSpeed');
  const linearValue = document.getElementById('linearValue');
  const angularValue = document.getElementById('angularValue');
  const repeatSend = document.getElementById('repeatSend');
  let holdTimer = null;
  let lastKeySentAt = 0;

  function show(data) {
    resultEl.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
  }

  function updateLabels() {
    linearValue.textContent = `${Number(linearInput.value).toFixed(2)} m/s`;
    angularValue.textContent = `${Number(angularInput.value).toFixed(2)} rad/s`;
  }

  async function postJson(url, body = {}) {
    show('Invio comando...');
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      show(data);
      return data;
    } catch (err) {
      show(`Errore invio comando: ${err}`);
      return { ok: false, error: String(err) };
    }
  }

  async function sendManualAction(action) {
    return postJson(`/api/drive/manual/${action}`, {});
  }

  async function sendDrive(linearScale, angularScale, source = 'manual-ui') {
    const linear = Number((Number(linearInput.value) * linearScale).toFixed(4));
    const angular = Number((Number(angularInput.value) * angularScale).toFixed(4));
    return postJson('/api/drive/command', { linear, angular, source });
  }

  function bindButton(button) {
    const ls = Number(button.dataset.linear || '0');
    const as = Number(button.dataset.angular || '0');
    const start = () => {
      sendDrive(ls, as);
      if (repeatSend.checked) holdTimer = window.setInterval(() => sendDrive(ls, as, 'manual-hold'), 250);
    };
    const stop = () => {
      if (holdTimer) { clearInterval(holdTimer); holdTimer = null; }
      if (repeatSend.checked) sendDrive(0, 0, 'manual-release');
    };
    button.addEventListener('mousedown', start);
    button.addEventListener('touchstart', (e) => { e.preventDefault(); start(); }, { passive: false });
    button.addEventListener('mouseup', stop);
    button.addEventListener('mouseleave', stop);
    button.addEventListener('touchend', stop);
  }

  document.querySelectorAll('.drive-btn').forEach(bindButton);
  document.getElementById('sendForwardBtn')?.addEventListener('click', () => sendDrive(1, 0));
  document.getElementById('sendBackwardBtn')?.addEventListener('click', () => sendDrive(-1, 0));
  document.getElementById('sendLeftBtn')?.addEventListener('click', () => sendDrive(0, 1));
  document.getElementById('sendRightBtn')?.addEventListener('click', () => sendDrive(0, -1));
  document.getElementById('stopAllBtn')?.addEventListener('click', () => sendManualAction('stop'));
  document.getElementById('takeControlBtn')?.addEventListener('click', () => sendManualAction('take'));
  document.getElementById('releaseControlBtn')?.addEventListener('click', () => sendManualAction('release'));
  document.getElementById('pauseBtn')?.addEventListener('click', () => sendManualAction('pause'));
  document.getElementById('readyBtn')?.addEventListener('click', () => sendManualAction('ready'));

  window.addEventListener('keydown', (e) => {
    const now = Date.now();
    if (now - lastKeySentAt < 180) return;
    lastKeySentAt = now;
    if (['ArrowUp', 'w', 'W'].includes(e.key)) sendDrive(1, 0, 'kbd');
    else if (['ArrowDown', 's', 'S'].includes(e.key)) sendDrive(-1, 0, 'kbd');
    else if (['ArrowLeft', 'a', 'A'].includes(e.key)) sendDrive(0, 1, 'kbd');
    else if (['ArrowRight', 'd', 'D'].includes(e.key)) sendDrive(0, -1, 'kbd');
    else if (e.code === 'Space') { e.preventDefault(); sendDrive(0, 0, 'kbd-stop'); }
  });
  linearInput?.addEventListener('input', updateLabels);
  angularInput?.addEventListener('input', updateLabels);
  updateLabels();
})();
