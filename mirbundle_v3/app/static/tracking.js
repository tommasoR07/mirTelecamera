(() => {
  const $ = (id) => document.getElementById(id);

  const streamImg = $('trackingStream');
  const overlay = $('trackingOverlay');
  const ctx = overlay ? overlay.getContext('2d') : null;
  const connectBtn = $('connectStreamBtn');
  const detectBtn = $('detectTagBtn');
  const startFollowBtn = $('startTagFollowBtn');
  const stopFollowBtn = $('stopFollowBtn');
  const clearBtn = $('clearTargetBtn');
  const saveSettingsBtn = $('saveTrackingSettingsBtn');
  const totalResetBtn = $('totalResetBtn');
  const streamUrlInput = $('streamUrlInput');
  const snapshotUrlInput = $('snapshotUrlInput');
  const mirHostInput = $('mirHostInput');
  const streamStateEl = $('trackingStreamState');
  const rosStateEl = $('trackingRosState');
  const targetStateEl = $('trackingTargetState');
  const tagIdEl = $('trackingTagId');
  const offsetEl = $('trackingOffset');
  const ratioEl = $('trackingRatio');
  const perfEl = $('trackingPerf');
  const commandEl = $('trackingCommand');
  const followStateEl = $('trackingFollowState');
  const desiredRatioInput = $('trackingTargetSize');
  const desiredRatioValue = $('trackingTargetSizeValue');
  const maxLinearInput = $('trackingLinearGain');
  const maxAngularInput = $('trackingAngularGain');
  const pidHzInput = $('trackingPidHz');
  const detectWidthInput = $('trackingDetectWidth');
  const pidInputs = {
    linearKp: $('trackingPidLinearKp'),
    linearKi: $('trackingPidLinearKi'),
    linearKd: $('trackingPidLinearKd'),
    angularKp: $('trackingPidAngularKp'),
    angularKi: $('trackingPidAngularKi'),
    angularKd: $('trackingPidAngularKd'),
  };
  const STORAGE_KEY = 'mir.trackingPid.v1';

  let selectedTagId = null;
  let followEnabled = false;
  let followTimer = null;
  let busy = false;
  let socket = null;
  let advertised = false;
  let joystickToken = '';
  let lastSeenAt = 0;
  let pid = resetPid();

  function resetPid() {
    return {
      lastTs: 0,
      distanceIntegral: 0,
      distancePrev: 0,
      offsetIntegral: 0,
      offsetPrev: 0,
      filteredOffset: null,
      visibleFrames: 0,
      missedFrames: 0,
      angularBrakeUntil: 0,
      lastOffsetSign: 0,
      lastLoopAt: 0,
      lastLoopMs: 0,
    };
  }

  function setText(el, value) {
    if (el) el.textContent = value;
  }

  function number(el, fallback) {
    const value = Number(el?.value);
    return Number.isFinite(value) ? value : fallback;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function currentTrackingSettings() {
    return {
      targetSize: desiredRatioInput?.value || '32',
      maxLinear: maxLinearInput?.value || '1.50',
      maxAngular: maxAngularInput?.value || '1.50',
      pidHz: pidHzInput?.value || '30',
      detectWidth: detectWidthInput?.value || '640',
    };
  }

  function applyTrackingSettings(saved) {
    if (saved.targetSize) desiredRatioInput.value = saved.targetSize;
    if (saved.maxLinear) maxLinearInput.value = saved.maxLinear;
    if (saved.maxAngular) maxAngularInput.value = saved.maxAngular;
    if (saved.pidHz) pidHzInput.value = saved.pidHz;
    if (saved.detectWidth) detectWidthInput.value = saved.detectWidth;
    updatePresetActiveState();
  }

  function updateTargetSizeLabel() {
    setText(desiredRatioValue, `${desiredRatioInput?.value || 18}%`);
    saveTrackingSettings(false);
  }

  async function saveTrackingSettings(persistServer = false) {
    const settings = currentTrackingSettings();
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    if (!persistServer) return;
    try {
      const res = await fetch('/api/tracking/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      });
      const data = await res.json().catch(() => ({}));
      if (data.ok && data.settings) applyTrackingSettings(data.settings);
      setText(followStateEl, data.ok ? 'valori tracking salvati su JSON' : 'errore salvataggio tracking');
    } catch (err) {
      setText(followStateEl, `errore salvataggio tracking: ${err}`);
    }
  }

  async function loadTrackingSettings() {
    try {
      const res = await fetch('/api/tracking/settings');
      const data = await res.json();
      if (data.ok && data.settings) {
        applyTrackingSettings(data.settings);
        localStorage.setItem(STORAGE_KEY, JSON.stringify(data.settings));
        return;
      }
    } catch (_) {}
    try {
      applyTrackingSettings(JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'));
    } catch (_) {}
  }

  function updatePresetActiveState() {
    document.querySelectorAll('.tracking-preset').forEach((button) => {
      const active = button.dataset.hz === pidHzInput?.value && button.dataset.detectWidth === detectWidthInput?.value;
      button.classList.toggle('active', active);
    });
  }

  function buildSocketUrl() {
    const rawHost = (mirHostInput?.value || '').trim();
    if (!rawHost) return '';
    let url;
    try {
      url = new URL(rawHost.includes('://') ? rawHost : `http://${rawHost}`);
    } catch (_) {
      return '';
    }
    return window.location.protocol === 'https:'
      ? `wss://${url.hostname}:443/rosbridge/`
      : `ws://${url.hostname}:9090`;
  }

  function rosSend(obj) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(obj));
    return true;
  }

  function advertiseJoystick() {
    advertised = rosSend({
      op: 'advertise',
      topic: '/joystick_vel',
      type: 'mirMsgs/JoystickVel',
    });
  }

  function requestManualControl() {
    const webSessionId = `aruco_tracker_${Date.now()}`;
    rosSend({
      op: 'call_service',
      service: '/mirsupervisor/setRobotState',
      type: 'mirSupervisor/SetState',
      args: {
        robotState: 11,
        web_session_id: webSessionId,
      },
      id: `tracking_manual_${Date.now()}`,
    });
  }

  function ensureRosBridge() {
    return new Promise((resolve) => {
      if (socket && socket.readyState === WebSocket.OPEN && joystickToken) {
        resolve(true);
        return;
      }
      const socketUrl = buildSocketUrl();
      if (!socketUrl) {
        setText(rosStateEl, 'host MiR non configurato');
        resolve(false);
        return;
      }
      if (socket) {
        try { socket.close(); } catch (_) {}
      }
      joystickToken = '';
      advertised = false;
      setText(rosStateEl, 'connessione ROSBridge...');
      socket = new WebSocket(socketUrl);
      const timeout = window.setTimeout(() => resolve(false), 3500);
      socket.addEventListener('open', () => {
        setText(rosStateEl, 'connesso, richiesta joystick...');
        advertiseJoystick();
        requestManualControl();
      });
      socket.addEventListener('message', (event) => {
        let data = null;
        try { data = JSON.parse(event.data); } catch (_) {}
        const token = data?.values?.joystick_token || data?.result?.joystick_token;
        if (token) {
          joystickToken = token;
          window.clearTimeout(timeout);
          setText(rosStateEl, 'joystick attivo');
          resolve(true);
        }
      });
      socket.addEventListener('close', () => {
        advertised = false;
        setText(rosStateEl, 'ROSBridge disconnesso');
      });
      socket.addEventListener('error', () => {
        setText(rosStateEl, 'errore ROSBridge');
        window.clearTimeout(timeout);
        resolve(false);
      });
    });
  }

  function publishVelocity(linear, angular) {
    if (!advertised) advertiseJoystick();
    if (!joystickToken) return false;
    const ok = rosSend({
      op: 'publish',
      topic: '/joystick_vel',
      msg: {
        joystick_token: joystickToken,
        speed_command: {
          linear: { x: linear, y: 0, z: 0 },
          angular: { x: 0, y: 0, z: angular },
        },
      },
    });
    if (ok) setText(commandEl, `linear=${linear.toFixed(3)} angular=${angular.toFixed(3)}`);
    return ok;
  }

  function stopRobot() {
    publishVelocity(0, 0);
    pid = resetPid();
  }

  function ensureCanvas() {
    if (!streamImg || !overlay || !ctx) return false;
    const rect = streamImg.getBoundingClientRect();
    const w = streamImg.naturalWidth || Math.round(rect.width) || 640;
    const h = streamImg.naturalHeight || Math.round(rect.height) || 480;
    if (!w || !h) return false;
    if (overlay.width !== w || overlay.height !== h) {
      overlay.width = w;
      overlay.height = h;
    }
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    return true;
  }

  function drawGuide() {
    if (!ensureCanvas()) return;
    ctx.strokeStyle = 'rgba(255,255,255,0.55)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(overlay.width / 2, 0);
    ctx.lineTo(overlay.width / 2, overlay.height);
    ctx.stroke();
  }

  function drawTag(tag) {
    if (!ensureCanvas()) return;
    drawGuide();
    if (!tag) return;
    const pts = tag.corners || [];
    ctx.strokeStyle = '#22c55e';
    ctx.lineWidth = 5;
    if (pts.length === 4) {
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      ctx.closePath();
      ctx.stroke();
    } else {
      ctx.strokeRect(tag.x, tag.y, tag.w, tag.h);
    }
    ctx.fillStyle = '#22c55e';
    ctx.font = '22px sans-serif';
    ctx.fillText(`APRILTAG ID ${tag.id}`, tag.x + 6, Math.max(26, tag.y - 8));
  }

  function computePid(command) {
    const nowMs = performance.now();
    const now = nowMs / 1000;
    const dt = pid.lastTs ? clamp(now - pid.lastTs, 0.02, 0.20) : 0.05;
    pid.lastTs = now;

    const desired = number(desiredRatioInput, 18) / 100;
    const sizeRatio = Number(command.size_ratio);
    const rawOffset = Number(command.offset_x);
    if (!Number.isFinite(sizeRatio) || !Number.isFinite(rawOffset)) return { linear: 0, angular: 0 };
    pid.filteredOffset = pid.filteredOffset === null
      ? rawOffset
      : pid.filteredOffset * 0.72 + rawOffset * 0.28;
    const offset = pid.filteredOffset;

    const distanceError = desired - sizeRatio;
    const offsetError = offset;
    const distanceDeadband = 0.030;
    const offsetDeadband = 0.130;
    const offsetSign = Math.abs(offsetError) > offsetDeadband ? Math.sign(offsetError) : 0;
    if (offsetSign && pid.lastOffsetSign && offsetSign !== pid.lastOffsetSign && Math.abs(offsetError) < 0.35) {
      pid.angularBrakeUntil = nowMs + 220;
      pid.offsetIntegral = 0;
    }
    if (offsetSign) pid.lastOffsetSign = offsetSign;

    pid.distanceIntegral = clamp(pid.distanceIntegral + distanceError * dt, -0.35, 0.35);
    pid.offsetIntegral = clamp(pid.offsetIntegral + offsetError * dt, -0.45, 0.45);
    const distanceDerivative = (distanceError - pid.distancePrev) / dt;
    const offsetDerivative = (offsetError - pid.offsetPrev) / dt;
    pid.distancePrev = distanceError;
    pid.offsetPrev = offsetError;

    const maxLinear = number(maxLinearInput, 1.5);
    const maxAngular = number(maxAngularInput, 1.5);
    const angularWindow = 0.55;

    let linear = 0;
    if (distanceError > distanceDeadband) {
      const effort =
        number(pidInputs.linearKp, 7.5) * distanceError +
        number(pidInputs.linearKi, 0) * pid.distanceIntegral +
        number(pidInputs.linearKd, 0.45) * Math.max(0, distanceDerivative);
      linear = maxLinear * clamp(Math.abs(effort), 0.25, 1);
    } else if (distanceError < -distanceDeadband * 1.8) {
      linear = -maxLinear * 0.20;
    }

    let angular = 0;
    if (nowMs < pid.angularBrakeUntil) {
      angular = 0;
    } else if (pid.visibleFrames >= 2 && Math.abs(offsetError) > offsetDeadband) {
      const effort =
        number(pidInputs.angularKp, 6.5) * (Math.abs(offsetError) - offsetDeadband) +
        number(pidInputs.angularKi, 0) * Math.abs(pid.offsetIntegral);
      const shaped = clamp(effort, 0, 1) * clamp(Math.abs(offsetError) / angularWindow, 0, 1);
      angular = -Math.sign(offsetError) * maxAngular * shaped;
    }

    linear = clamp(linear, -maxLinear * 0.20, maxLinear);
    const nearCenterLimit = maxAngular * clamp((Math.abs(offsetError) - offsetDeadband) / angularWindow, 0, 1);
    angular = clamp(angular, -nearCenterLimit, nearCenterLimit);
    return { linear, angular };
  }

  function updateStats(data) {
    if (!data || !data.ok) {
      const msg = data?.error || 'errore rilevamento AprilTag';
      setText(targetStateEl, msg);
      setText(offsetEl, '-');
      setText(ratioEl, '-');
      setText(perfEl, '-');
      drawGuide();
      pid.missedFrames += 1;
      pid.visibleFrames = 0;
      if (followEnabled) stopRobot();
      return null;
    }
    const tag = data.tag || null;
    drawTag(tag);
    if (!tag) {
      setText(targetStateEl, selectedTagId === null ? 'nessun AprilTag rilevato' : `AprilTag ID ${selectedTagId} non visibile`);
      pid.missedFrames += 1;
      pid.visibleFrames = 0;
      if (followEnabled) stopRobot();
      return null;
    }
    lastSeenAt = performance.now();
    pid.visibleFrames += 1;
    pid.missedFrames = 0;
    if (selectedTagId !== null) setText(tagIdEl, selectedTagId);
    const cmd = data.command || {};
    setText(targetStateEl, `rilevato AprilTag ID ${tag.id}`);
    setText(offsetEl, cmd.offset_x ?? '-');
    setText(ratioEl, cmd.size_ratio ?? '-');
    const perf = data.perf || {};
    const actualHz = pid.lastLoopMs ? `${(1000 / pid.lastLoopMs).toFixed(1)} Hz` : '-';
    setText(perfEl, `det ${perf.detect_ms ?? '-'} ms | frame ${perf.frame_age_ms ?? '-'} ms | loop ${actualHz}`);
    return cmd;
  }

  function currentPayload(acquireTarget) {
    const streamUrl = (streamUrlInput?.value || '').trim();
    const streamIsVideo = /^(rtsp|rtmp):\/\//i.test(streamUrl) || /\/(stream|mjpeg|mjpg|video|video_feed)\b/i.test(streamUrl) || /[?&]action=stream/i.test(streamUrl);
    return {
      stream_url: streamUrl,
      snapshot_url: streamIsVideo ? '' : (snapshotUrlInput?.value || '').trim(),
      acquire_target: !!acquireTarget,
      target_id: selectedTagId,
      desired_size_ratio: number(desiredRatioInput, 18) / 100,
      max_linear: number(maxLinearInput, 0.18),
      max_angular: number(maxAngularInput, 0.45),
      max_detect_width: Math.max(320, Math.min(1080, number(detectWidthInput, 640))),
    };
  }

  async function tagStep(acquireTarget) {
    if (busy) return null;
    busy = true;
    try {
      const res = await fetch('/api/tracking/apriltag-step', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(currentPayload(acquireTarget)),
      });
      const text = await res.text();
      let data;
      try { data = JSON.parse(text); } catch (_) { data = { ok: false, error: text || `HTTP ${res.status}` }; }
      if (data.ok && data.acquired_tag_id !== undefined && data.acquired_tag_id !== null) {
        selectedTagId = data.acquired_tag_id;
        setText(tagIdEl, selectedTagId);
      }
      return updateStats(data);
    } catch (err) {
      setText(targetStateEl, `errore chiamata backend: ${err}`);
      drawGuide();
      return null;
    } finally {
      busy = false;
    }
  }

  function connectStream() {
    const url = (streamUrlInput?.value || '').trim();
    if (!url) {
      setText(streamStateEl, 'manca URL stream');
      return;
    }
    if (/^(rtsp|rtmp):\/\//i.test(url)) {
      setText(streamStateEl, 'stream usato dal backend; anteprima browser non disponibile');
      drawGuide();
      return;
    }
    const isMjpeg = /\/(stream|mjpeg|mjpg|video|video_feed)\b/i.test(url) || /[?&]action=stream/i.test(url);
    streamImg.onerror = () => setText(streamStateEl, isMjpeg ? 'errore stream MJPEG' : 'errore snapshot');
    streamImg.onload = () => {
      setText(streamStateEl, isMjpeg ? 'stream MJPEG live connesso' : 'snapshot connesso');
      drawGuide();
    };
    streamImg.src = isMjpeg ? url : `${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`;
    setTimeout(drawGuide, 600);
  }

  async function detectTag() {
    followEnabled = false;
    window.clearTimeout(followTimer);
    selectedTagId = null;
    pid = resetPid();
    setText(tagIdEl, '-');
    setText(followStateEl, 'riconoscimento AprilTag...');
    await tagStep(true);
  }

  async function startFollow() {
    if (selectedTagId === null) {
      setText(followStateEl, 'prima premi Riconosci AprilTag');
      drawGuide();
      return;
    }
    const ok = await ensureRosBridge();
    if (!ok) {
      setText(followStateEl, 'ROSBridge non pronto');
      return;
    }
    followEnabled = true;
    pid = resetPid();
    lastSeenAt = performance.now();
    setText(followStateEl, `PID attivo su AprilTag ID ${selectedTagId}`);
    followLoop();
  }

  async function followLoop() {
    if (!followEnabled) return;
    const started = performance.now();
    if (pid.lastLoopAt) pid.lastLoopMs = started - pid.lastLoopAt;
    pid.lastLoopAt = started;
    const cmd = await tagStep(false);
    if (followEnabled && cmd) {
      const out = computePid(cmd);
      publishVelocity(out.linear, out.angular);
    }
    const elapsed = performance.now() - started;
    const targetMs = 1000 / Math.max(1, Math.min(60, number(pidHzInput, 30)));
    followTimer = window.setTimeout(followLoop, Math.max(8, targetMs - elapsed));
  }

  function stopFollow() {
    followEnabled = false;
    window.clearTimeout(followTimer);
    stopRobot();
    setText(followStateEl, 'spento');
  }

  async function totalReset() {
    followEnabled = false;
    window.clearTimeout(followTimer);
    stopRobot();
    selectedTagId = null;
    pid = resetPid();
    busy = false;
    advertised = false;
    joystickToken = '';
    if (socket) {
      try { socket.close(); } catch (_) {}
    }
    socket = null;
    if (streamImg) {
      streamImg.removeAttribute('src');
      streamImg.onload = null;
      streamImg.onerror = null;
    }
    setText(tagIdEl, '-');
    setText(offsetEl, '-');
    setText(ratioEl, '-');
    setText(perfEl, '-');
    setText(rosStateEl, 'reset');
    setText(targetStateEl, 'reset completato');
    setText(followStateEl, 'reset totale...');
    drawGuide();
    try {
      await fetch('/api/tracking/reset', { method: 'POST' });
    } catch (_) {}
    connectStream();
    setText(followStateEl, 'reset totale completato');
  }

  function applyPreset(button) {
    document.querySelectorAll('.tracking-preset').forEach((el) => el.classList.remove('active'));
    button.classList.add('active');
    maxLinearInput.value = button.dataset.linear || maxLinearInput.value;
    maxAngularInput.value = button.dataset.angular || maxAngularInput.value;
    pidHzInput.value = button.dataset.hz || pidHzInput.value;
    detectWidthInput.value = button.dataset.detectWidth || detectWidthInput.value;
    saveTrackingSettings(false);
  }

  clearBtn?.addEventListener('click', () => {
    selectedTagId = null;
    pid = resetPid();
    setText(tagIdEl, '-');
    setText(targetStateEl, 'target rimosso');
    drawGuide();
  });
  connectBtn?.addEventListener('click', connectStream);
  detectBtn?.addEventListener('click', detectTag);
  startFollowBtn?.addEventListener('click', startFollow);
  stopFollowBtn?.addEventListener('click', stopFollow);
  totalResetBtn?.addEventListener('click', totalReset);
  desiredRatioInput?.addEventListener('input', updateTargetSizeLabel);
  maxLinearInput?.addEventListener('input', () => saveTrackingSettings(false));
  maxAngularInput?.addEventListener('input', () => saveTrackingSettings(false));
  pidHzInput?.addEventListener('input', () => saveTrackingSettings(false));
  detectWidthInput?.addEventListener('input', () => saveTrackingSettings(false));
  saveSettingsBtn?.addEventListener('click', () => saveTrackingSettings(true));
  document.querySelectorAll('.tracking-preset').forEach((button) => {
    button.addEventListener('click', () => applyPreset(button));
  });
  loadTrackingSettings().finally(() => {
    updatePresetActiveState();
    updateTargetSizeLabel();
  });
  setText(followStateEl, 'pronto');
  drawGuide();
})();
