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

  let socket = null;
  let connected = false;
  let advertised = false;
  let holdTimer = null;
  let autoStopTimer = null;

  function setText(el, value) {
    if (el) el.textContent = value;
  }

  function log(message, data) {
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
    if (!sessionInput.value.trim()) sessionInput.value = `aruco_tracker_${Date.now()}`;
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
    const webSessionId = (sessionInput.value || '').trim() || `aruco_tracker_${Date.now()}`;
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
    const multiplier = turboOverride?.checked ? 1.75 : 1;
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
    window.clearTimeout(autoStopTimer);
    holdTimer = null;
    autoStopTimer = null;
    if (socket && socket.readyState === WebSocket.OPEN) {
      publishVelocity(0, 0);
    }
  }

  function pulse(linearScale, angularScale, durationMs = 1000) {
    const v = values(linearScale, angularScale);
    publishVelocity(v.linear, v.angular);
    window.clearTimeout(autoStopTimer);
    autoStopTimer = window.setTimeout(stopPublish, durationMs);
  }

  function bindHold(button) {
    const linearScale = Number(button.dataset.linear || 0);
    const angularScale = Number(button.dataset.angular || 0);
    const start = () => {
      const v = values(linearScale, angularScale);
      publishVelocity(v.linear, v.angular);
      window.clearInterval(holdTimer);
      holdTimer = window.setInterval(() => publishVelocity(v.linear, v.angular), 100);
    };
    const stop = () => stopPublish();
    button.addEventListener('mousedown', start);
    button.addEventListener('touchstart', (event) => {
      event.preventDefault();
      start();
    }, { passive: false });
    button.addEventListener('mouseup', stop);
    button.addEventListener('mouseleave', stop);
    button.addEventListener('touchend', stop);
  }

  function updateLabels() {
    const multiplier = turboOverride?.checked ? 1.75 : 1;
    setText(linearValue, `${(Number(linearInput.value || 0) * multiplier).toFixed(2)}${multiplier > 1 ? ' turbo' : ''}`);
    setText(angularValue, `${(Number(angularInput.value || 0) * multiplier).toFixed(2)}${multiplier > 1 ? ' turbo' : ''}`);
  }

  function applySpeedProfile(button) {
    document.querySelectorAll('.ros-speed-profile').forEach((el) => el.classList.remove('active'));
    button.classList.add('active');
    linearInput.value = button.dataset.linear || linearInput.value;
    angularInput.value = button.dataset.angular || angularInput.value;
    updateLabels();
  }

  $('connectRosBtn')?.addEventListener('click', connect);
  $('disconnectRosBtn')?.addEventListener('click', () => disconnect(true));
  $('rosStopBtn')?.addEventListener('click', stopPublish);
  $('rosForwardBtn')?.addEventListener('click', () => pulse(1, 0));
  $('rosLeftBtn')?.addEventListener('click', () => pulse(0, 1));
  $('rosRightBtn')?.addEventListener('click', () => pulse(0, -1));
  document.querySelectorAll('.ros-drive-btn').forEach(bindHold);
  document.querySelectorAll('.ros-speed-profile').forEach((button) => {
    button.addEventListener('click', () => applySpeedProfile(button));
  });
  linearInput?.addEventListener('input', updateLabels);
  angularInput?.addEventListener('input', updateLabels);
  turboOverride?.addEventListener('change', updateLabels);
  updateLabels();
  buildSocketUrl();
})();
