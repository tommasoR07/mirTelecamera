(() => {
  const $ = (id) => document.getElementById(id);

  const mirHostInput = $('mirHostInput');
  const socketUrlInput = $('rosSocketUrlInput');
  const sessionInput = $('rosSessionInput');
  const tokenInput = $('joystickTokenInput');
  const stateEl = $('rosState');
  const logEl = $('rosLog');
  const lastCommandEl = $('rosLastCommand');
  const linearInput = $('rosLinearSpeed');
  const angularInput = $('rosAngularSpeed');
  const linearValue = $('rosLinearValue');
  const angularValue = $('rosAngularValue');
  const turboOverride = $('rosTurboOverride');
  const joystick = $('rosJoystick');
  const joystickKnob = $('rosJoystickKnob');
  const joystickStream = $('joystickStream');
  const toggleStreamBtn = $('toggleJoystickStreamBtn');
  const cameraStreamUrlInput = $('cameraStreamUrl');
  const STORAGE_KEY = 'mir.joystickLab.v1';

  let socket = null;
  let connected = false;
  let advertised = false;
  let holdTimer = null;
  let joystickPointerId = null;
  let joystickVector = { linearScale: 0, angularScale: 0 };

  function saveSettings() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      linear: linearInput.value,
      angular: angularInput.value,
      turbo: !!turboOverride?.checked,
    }));
  }

  function loadSettings() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      if (saved.linear) linearInput.value = saved.linear;
      if (saved.angular) angularInput.value = saved.angular;
      if (turboOverride) turboOverride.checked = !!saved.turbo;
    } catch (_) {}
  }

  function setText(el, value) {
    if (el) el.textContent = value;
  }

  function log(message, data) {
    if (!logEl) return;
    const stamp = new Date().toLocaleTimeString();
    const line = data === undefined
      ? `[${stamp}] ${message}`
      : `[${stamp}] ${message}\n${JSON.stringify(data, null, 2)}`;
    logEl.textContent = `${line}\n\n${logEl.textContent || ''}`.slice(0, 12000);
  }

  function send(obj) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      log('WebSocket non connesso');
      return false;
    }
    socket.send(JSON.stringify(obj));
    log('TX', obj);
    return true;
  }

  function buildSocketUrl() {
    const rawHost = (mirHostInput.value || '').trim();
    if (!rawHost) {
      setText(stateEl, 'manca Robot host');
      return;
    }
    let url;
    try {
      url = new URL(rawHost.includes('://') ? rawHost : `http://${rawHost}`);
    } catch (_) {
      setText(stateEl, 'Robot host non valido');
      return;
    }
    const securePage = window.location.protocol === 'https:';
    socketUrlInput.value = securePage
      ? `wss://${url.hostname}:443/rosbridge/`
      : `ws://${url.hostname}:9090`;
    if (!sessionInput.value.trim()) sessionInput.value = 'MIRITISCUNEO';
  }

  function connect() {
    buildSocketUrl();
    const url = (socketUrlInput.value || '').trim();
    if (!url) return;
    disconnect(false);
    setText(stateEl, 'connessione...');
    advertised = false;
    socket = new WebSocket(url);
    socket.addEventListener('open', () => {
      connected = true;
      setText(stateEl, 'connesso, richiesta manual control...');
      log('WebSocket aperto');
      advertise();
      requestManualControl();
    });
    socket.addEventListener('message', (event) => {
      let data = event.data;
      try { data = JSON.parse(event.data); } catch (_) {}
      log('RX', data);
      if (data && data.op === 'service_response') {
        const token = data.values?.joystick_token || data.result?.joystick_token;
        if (token) {
          tokenInput.value = token;
          setText(stateEl, 'manual control attivo, token ricevuto');
        }
      }
    });
    socket.addEventListener('close', () => {
      connected = false;
      advertised = false;
      setText(stateEl, 'disconnesso');
      log('WebSocket chiuso');
    });
    socket.addEventListener('error', (event) => {
      setText(stateEl, 'errore WebSocket');
      log('Errore WebSocket', { type: event.type });
    });
  }

  function disconnect(sendStop = true) {
    if (sendStop) stopPublish();
    if (socket) {
      try { socket.close(); } catch (_) {}
    }
    socket = null;
    connected = false;
    advertised = false;
  }

  function advertise() {
    if (!connected) return;
    advertised = send({
      op: 'advertise',
      topic: '/joystick_vel',
      type: 'mirMsgs/JoystickVel',
    });
  }

  function requestManualControl() {
    if (!connected) return;
    const webSessionId = (sessionInput.value || '').trim() || 'MIRITISCUNEO';
    sessionInput.value = webSessionId;
    send({
      op: 'call_service',
      service: '/mirsupervisor/setRobotState',
      type: 'mirSupervisor/SetState',
      args: {
        robotState: 11,
        web_session_id: webSessionId,
      },
      id: `set_manual_${Date.now()}`,
    });
  }

  function values(linearScale, angularScale) {
    const multiplier = turboOverride?.checked ? 1.5 : 1;
    return {
      linear: Number((Number(linearInput.value || 0) * multiplier * linearScale).toFixed(4)),
      angular: Number((Number(angularInput.value || 0) * multiplier * angularScale).toFixed(4)),
    };
  }

  function publishVelocity(linear, angular) {
    if (!advertised) advertise();
    const joystickToken = (tokenInput.value || '').trim();
    if (!joystickToken) {
      setText(stateEl, 'manca joystick_token');
      log('Prima richiedi Manual control o inserisci joystick_token');
      return;
    }
    const msg = {
      op: 'publish',
      topic: '/joystick_vel',
      msg: {
        joystick_token: joystickToken,
        speed_command: {
          linear: { x: linear, y: 0, z: 0 },
          angular: { x: 0, y: 0, z: angular },
        },
      },
    };
    if (send(msg)) {
      setText(lastCommandEl, `linear.x=${linear} angular.z=${angular}`);
    }
  }

  function stopPublish() {
    window.clearInterval(holdTimer);
    holdTimer = null;
    joystickVector = { linearScale: 0, angularScale: 0 };
    setKnob(0, 0);
    if (socket && socket.readyState === WebSocket.OPEN) {
      publishVelocity(0, 0);
    }
  }

  function setKnob(x, y) {
    if (!joystickKnob) return;
    joystickKnob.style.transform = `translate(calc(-50% + ${x}px), calc(-50% + ${y}px))`;
  }

  function updateJoystickFromPointer(event) {
    if (!joystick) return;
    const rect = joystick.getBoundingClientRect();
    const radius = Math.min(rect.width, rect.height) / 2;
    const knobRadius = 28;
    const max = Math.max(1, radius - knobRadius);
    let x = event.clientX - (rect.left + rect.width / 2);
    let y = event.clientY - (rect.top + rect.height / 2);
    const dist = Math.hypot(x, y);
    if (dist > max) {
      x = (x / dist) * max;
      y = (y / dist) * max;
    }
    setKnob(x, y);
    joystickVector = {
      linearScale: Number((-y / max).toFixed(3)),
      angularScale: Number((-x / max).toFixed(3)),
    };
    publishJoystickVector();
  }

  function publishJoystickVector() {
    const v = values(joystickVector.linearScale, joystickVector.angularScale);
    publishVelocity(v.linear, v.angular);
  }

  function startJoystick(event) {
    if (!joystick) return;
    event.preventDefault();
    joystickPointerId = event.pointerId;
    joystick.setPointerCapture(event.pointerId);
    updateJoystickFromPointer(event);
    window.clearInterval(holdTimer);
    holdTimer = window.setInterval(publishJoystickVector, 100);
  }

  function moveJoystick(event) {
    if (joystickPointerId !== event.pointerId) return;
    event.preventDefault();
    updateJoystickFromPointer(event);
  }

  function endJoystick(event) {
    if (joystickPointerId !== event.pointerId) return;
    joystickPointerId = null;
    stopPublish();
  }

  function updateLabels() {
    const multiplier = turboOverride?.checked ? 1.5 : 1;
    setText(linearValue, `${(Number(linearInput.value || 0) * multiplier).toFixed(2)}${multiplier > 1 ? ' turbo' : ''}`);
    setText(angularValue, `${(Number(angularInput.value || 0) * multiplier).toFixed(2)}${multiplier > 1 ? ' turbo' : ''}`);
    saveSettings();
  }

  function applySpeedProfile(button) {
    document.querySelectorAll('.ros-speed-profile').forEach((el) => el.classList.remove('active'));
    button.classList.add('active');
    linearInput.value = button.dataset.linear || linearInput.value;
    angularInput.value = button.dataset.angular || angularInput.value;
    updateLabels();
  }

  let streamActive = false;

  function toggleStream() {
    if (!joystickStream || !toggleStreamBtn || !cameraStreamUrlInput) return;
    const url = cameraStreamUrlInput.value.trim();
    if (!url) {
      toggleStreamBtn.textContent = 'Manca URL Stream';
      return;
    }
    if (streamActive) {
      joystickStream.removeAttribute('src');
      joystickStream.onload = null;
      joystickStream.onerror = null;
      toggleStreamBtn.textContent = 'Connetti Telecamera';
      toggleStreamBtn.classList.remove('danger');
      toggleStreamBtn.classList.add('primary');
      streamActive = false;
    } else {
      toggleStreamBtn.textContent = 'Connessione...';
      const isMjpeg = /\/(stream|mjpeg|mjpg|video|video_feed)\b/i.test(url) || /[?&]action=stream/i.test(url);
      joystickStream.onerror = () => {
        toggleStreamBtn.textContent = 'Errore Connessione';
        toggleStreamBtn.classList.remove('primary');
        toggleStreamBtn.classList.add('danger');
        streamActive = false;
      };
      joystickStream.onload = () => {
        toggleStreamBtn.textContent = 'Disconnetti Telecamera';
        toggleStreamBtn.classList.remove('primary');
        toggleStreamBtn.classList.add('danger');
        streamActive = true;
      };
      joystickStream.src = isMjpeg ? url : `${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`;
    }
  }

  $('connectRosBtn')?.addEventListener('click', connect);
  $('disconnectRosBtn')?.addEventListener('click', () => disconnect(true));
  $('rosStopBtn')?.addEventListener('click', stopPublish);
  toggleStreamBtn?.addEventListener('click', toggleStream);
  joystick?.addEventListener('pointerdown', startJoystick);
  joystick?.addEventListener('pointermove', moveJoystick);
  joystick?.addEventListener('pointerup', endJoystick);
  joystick?.addEventListener('pointercancel', endJoystick);
  document.querySelectorAll('.ros-speed-profile').forEach((button) => {
    button.addEventListener('click', () => applySpeedProfile(button));
  });
  linearInput?.addEventListener('input', updateLabels);
  angularInput?.addEventListener('input', updateLabels);
  turboOverride?.addEventListener('change', updateLabels);
  loadSettings();
  updateLabels();
  buildSocketUrl();
})();
