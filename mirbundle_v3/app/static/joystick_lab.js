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
    const url = (socketUrlInput.value || '').trim();
    if (!url) {
      buildSocketUrl();
      if (!socketUrlInput.value.trim()) return;
    }
    disconnect(false);
    setText(stateEl, 'connessione...');
    advertised = false;
    socket = new WebSocket(socketUrlInput.value.trim());
    socket.addEventListener('open', () => {
      connected = true;
      setText(stateEl, 'connesso');
      log('WebSocket aperto');
      advertise();
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
    if (!connected) connect();
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
    return {
      linear: Number((Number(linearInput.value || 0) * linearScale).toFixed(4)),
      angular: Number((Number(angularInput.value || 0) * angularScale).toFixed(4)),
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
    setText(linearValue, Number(linearInput.value || 0).toFixed(2));
    setText(angularValue, Number(angularInput.value || 0).toFixed(2));
  }

  $('buildSocketBtn')?.addEventListener('click', buildSocketUrl);
  $('connectRosBtn')?.addEventListener('click', connect);
  $('manualRosBtn')?.addEventListener('click', requestManualControl);
  $('advertiseRosBtn')?.addEventListener('click', advertise);
  $('disconnectRosBtn')?.addEventListener('click', () => disconnect(true));
  $('rosStopBtn')?.addEventListener('click', stopPublish);
  $('rosForwardBtn')?.addEventListener('click', () => pulse(1, 0));
  $('rosLeftBtn')?.addEventListener('click', () => pulse(0, 1));
  $('rosRightBtn')?.addEventListener('click', () => pulse(0, -1));
  document.querySelectorAll('.ros-drive-btn').forEach(bindHold);
  linearInput?.addEventListener('input', updateLabels);
  angularInput?.addEventListener('input', updateLabels);
  updateLabels();
  buildSocketUrl();
})();
