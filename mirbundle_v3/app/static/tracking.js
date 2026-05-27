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
  const precisionEl = $('trackingPrecision');
  const curveModeEl = $('trackingCurveMode');
  const perfEl = $('trackingPerf');
  const commandEl = $('trackingCommand');
  const followStateEl = $('trackingFollowState');
  const profilerLoopEl = $('profilerLoop');
  const profilerFetchEl = $('profilerFetch');
  const profilerDetectEl = $('profilerDetect');
  const profilerFrameEl = $('profilerFrame');
  const profilerMissEl = $('profilerMiss');
  const profilerDictEl = $('profilerDict');
  const barLoopEl = $('barLoop');
  const barFetchEl = $('barFetch');
  const barDetectEl = $('barDetect');
  const barTotalEl = $('barTotal');
  const barFrameEl = $('barFrame');
  const barStabilityEl = $('barStability');
  const profilerStabilityValEl = $('profilerStabilityVal');
  const barDictEl = $('barDict');
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
  if (streamImg) {
    streamImg.decoding = 'async';
    streamImg.loading = 'eager';
  }

  let selectedTagId = null;
  let followEnabled = false;
  let followTimer = null;
  let busy = false;
  let socket = null;
  let advertised = false;
  let joystickToken = '';
  let lastSeenAt = 0;
  let lastUiAt = 0;
  let lastOverlayAt = 0;
  let lastBrakeAt = 0;
  let lastCommandUiAt = 0;
  let lastBackendFrameId = 0;
  let rosReconnectAt = 0;
  let selectedTagDictionary = '';
  let lastTag = null;
  let lastTagSampleAt = 0;
  let pid = resetPid();

  function resetPid() {
    return {
      lastTs: 0,
      distanceIntegral: 0,
      distancePrev: 0,
      offsetIntegral: 0,
      offsetPrev: 0,
      filteredOffset: null,
      filteredSize: null,
      offsetVelocity: 0,
      sizeVelocity: 0,
      visibleFrames: 0,
      missedFrames: 0,
      angularBrakeUntil: 0,
      lastOffsetSign: 0,
      lastOscillationAt: 0,
      oscillationScore: 0,
      chillStopUntil: 0,
      chillForwardUntil: 0,
      curveInPlace: false,
      lastLinear: 0,
      lastAngular: 0,
      lastLoopAt: 0,
      lastLoopMs: 0,
      backendErrorFrames: 0,
    };
  }

  function resetTransientTrackingState() {
    pid.lastTs = 0;
    pid.distanceIntegral = 0;
    pid.distancePrev = 0;
    pid.offsetIntegral = 0;
    pid.offsetPrev = 0;
    pid.filteredOffset = null;
    pid.filteredSize = null;
    pid.offsetVelocity = 0;
    pid.sizeVelocity = 0;
    pid.angularBrakeUntil = 0;
    pid.lastOffsetSign = 0;
    pid.lastOscillationAt = 0;
    pid.oscillationScore = 0;
    pid.chillStopUntil = 0;
    pid.chillForwardUntil = 0;
    pid.curveInPlace = false;
    pid.lastLinear = 0;
    pid.lastAngular = 0;
  }

  function setText(el, value) {
    if (el) el.textContent = value;
  }

  function setBarColorClass(barEl, type) {
    if (!barEl) return;
    barEl.classList.remove('fill-success', 'fill-warning', 'fill-danger', 'fill-info', 'fill-primary', 'fill-secondary', 'fill-muted');
    barEl.classList.add(`fill-${type}`);
  }

  function number(el, fallback) {
    const value = Number(el?.value);
    return Number.isFinite(value) ? value : fallback;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function smoothstep(edge0, edge1, value) {
    const t = clamp((value - edge0) / Math.max(0.0001, edge1 - edge0), 0, 1);
    return t * t * (3 - 2 * t);
  }

  function currentTrackingSettings() {
    return {
      targetSize: desiredRatioInput?.value || '32',
      maxLinear: maxLinearInput?.value || '1.50',
      maxAngular: maxAngularInput?.value || '1.50',
      pidHz: pidHzInput?.value || '160',
      detectWidth: detectWidthInput?.value || '360',
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
    const webSessionId = 'MIRITISCUNEO';
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

  function publishVelocity(linear, angular, mode = '') {
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
    if (!ok) {
      advertised = false;
      setText(rosStateEl, 'ROSBridge instabile, retry...');
      return false;
    }
    if (ok) {
      const now = performance.now();
      if (now - lastCommandUiAt > 100 || (linear === 0 && angular === 0)) {
        lastCommandUiAt = now;
        const suffix = mode ? ` | ${mode}` : '';
        setText(commandEl, `linear=${linear.toFixed(3)} angular=${angular.toFixed(3)}${suffix}`);
      }
    }
    return ok;
  }

  function stopRobot() {
    publishVelocity(0, 0);
    pid = resetPid();
  }

  function safeBrake(mode = 'ricerca target') {
    const now = performance.now();
    if (now - lastBrakeAt < 90) return;
    lastBrakeAt = now;
    publishVelocity(0, 0, mode);
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
    const now = performance.now();
    if (now - lastOverlayAt < 70) return;
    lastOverlayAt = now;
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

  function gateMode(absOffset, absAngular) {
    if (absOffset > 0.18) return 'riallineamento';
    if (absAngular > 0.18) return 'micro-correzione';
    return '';
  }

  function computePid(command) {
    const nowMs = performance.now();
    const now = nowMs / 1000;
    const dt = pid.lastTs ? clamp(now - pid.lastTs, 0.008, 0.10) : 0.016;
    pid.lastTs = now;

    const desired = number(desiredRatioInput, 18) / 100;
    const sizeRatio = Number(command.size_ratio);
    const rawOffset = Number(command.offset_x);
    if (!Number.isFinite(sizeRatio) || !Number.isFinite(rawOffset)) return { linear: 0, angular: 0 };

    const offsetAlpha = clamp(dt / (0.006 + dt), 0.45, 0.90);
    const sizeAlpha = clamp(dt / (0.012 + dt), 0.35, 0.82);
    const previousOffset = pid.filteredOffset ?? rawOffset;
    const previousSize = pid.filteredSize ?? sizeRatio;
    pid.filteredOffset = previousOffset + (rawOffset - previousOffset) * offsetAlpha;
    pid.filteredSize = previousSize + (sizeRatio - previousSize) * sizeAlpha;

    const rawOffsetVelocity = (pid.filteredOffset - previousOffset) / dt;
    const rawSizeVelocity = (pid.filteredSize - previousSize) / dt;
    const velocityAlpha = clamp(dt / (0.014 + dt), 0.32, 0.78);
    pid.offsetVelocity += (rawOffsetVelocity - pid.offsetVelocity) * velocityAlpha;
    pid.sizeVelocity += (rawSizeVelocity - pid.sizeVelocity) * velocityAlpha;

    const predictionLead = clamp(0.028 + dt * 1.8, 0.030, 0.085);
    const offsetError = clamp(pid.filteredOffset + pid.offsetVelocity * predictionLead, -1.15, 1.15);
    const distanceError = desired - pid.filteredSize;
    const maxLinear = number(maxLinearInput, 1.5);
    const maxAngular = number(maxAngularInput, 1.5);

    const offsetDeadband = 0.030;
    const distanceDeadband = 0.012;
    const offsetSign = Math.abs(offsetError) > offsetDeadband ? Math.sign(offsetError) : 0;
    const fastFlip = offsetSign && pid.lastOffsetSign && offsetSign !== pid.lastOffsetSign && nowMs - pid.lastOscillationAt < 520;
    if (fastFlip && Math.abs(pid.lastAngular) > maxAngular * 0.18 && Math.abs(lateralVelocity) > 0.55) {
      pid.oscillationScore = Math.min(5, pid.oscillationScore + 1);
      if (pid.oscillationScore >= 2) {
        pid.chillStopUntil = nowMs + 360;
        pid.chillForwardUntil = nowMs + 1250;
        pid.offsetIntegral = 0;
        pid.distanceIntegral = 0;
      }
    } else if (!fastFlip && nowMs - pid.lastOscillationAt > 700) {
      pid.oscillationScore = Math.max(0, pid.oscillationScore - 0.35);
    }
    if (offsetSign && pid.lastOffsetSign && offsetSign !== pid.lastOffsetSign) {
      pid.lastOscillationAt = nowMs;
    }
    if (offsetSign && pid.lastOffsetSign && offsetSign !== pid.lastOffsetSign && Math.abs(offsetError) < 0.045) {
      pid.angularBrakeUntil = nowMs + 18;
      pid.offsetIntegral = 0;
    }
    if (offsetSign) pid.lastOffsetSign = offsetSign;

    const absOffset = Math.abs(offsetError);
    const absDistance = Math.abs(distanceError);
    const lateralVelocity = pid.offsetVelocity;
    const distanceVelocity = -pid.sizeVelocity;
    const closingTooFast = distanceError > 0 ? Math.max(0, -distanceVelocity) : Math.max(0, distanceVelocity);

    const curveEnter = 0.20;
    const curveExit = 0.10;
    pid.curveInPlace = absOffset > curveEnter || (pid.curveInPlace && absOffset > curveExit);

    const allowAngularIntegral = !pid.curveInPlace && absOffset < 0.26 && absOffset > offsetDeadband;
    pid.offsetIntegral = allowAngularIntegral ? clamp(pid.offsetIntegral + offsetError * dt, -0.18, 0.18) : 0;
    const allowLinearIntegral = absOffset < 0.16 && absDistance < 0.16 && absDistance > distanceDeadband;
    pid.distanceIntegral = allowLinearIntegral ? clamp(pid.distanceIntegral + distanceError * dt, -0.16, 0.16) : 0;

    pid.distancePrev = distanceError;
    pid.offsetPrev = offsetError;

    let angularTarget = 0;
    if (pid.curveInPlace) {
      const curveDemand = smoothstep(curveExit, 0.72, absOffset);
      const damping = clamp(1 - Math.max(0, -Math.sign(offsetError || 1) * lateralVelocity) * 0.028, 0.58, 1);
      angularTarget = -Math.sign(offsetError || 1) * maxAngular * clamp(0.40 + curveDemand * 0.60, 0, 1.00) * damping;
    } else if (nowMs < pid.angularBrakeUntil && absOffset < 0.045) {
      angularTarget = 0;
    } else if (absOffset > offsetDeadband) {
      const kp = number(pidInputs.angularKp, 2.60);
      const ki = number(pidInputs.angularKi, 0.00);
      const kd = number(pidInputs.angularKd, 0.08);
      const normalized = kp * offsetError + ki * pid.offsetIntegral + kd * lateralVelocity;
      const authority = smoothstep(offsetDeadband, 0.34, absOffset);
      angularTarget = -maxAngular * clamp(normalized, -1, 1) * clamp(0.28 + authority * 0.72, 0, 1);
      const minTurn = maxAngular * clamp(0.055 + authority * 0.10, 0, 0.16);
      if (Math.abs(angularTarget) < minTurn) {
        angularTarget = -Math.sign(offsetError || 1) * minTurn;
      }
    }

    let linearTarget = 0;
    if (!pid.curveInPlace) {
      const alignmentGate = 1 - smoothstep(0.28, 0.72, absOffset);
      const angularGate = 1 - smoothstep(maxAngular * 0.62, maxAngular * 1.00, Math.abs(pid.lastAngular));
      const gate = clamp(alignmentGate * angularGate, 0, 1);
      if (distanceError > distanceDeadband) {
        const kp = number(pidInputs.linearKp, 3.20);
        const ki = number(pidInputs.linearKi, 0.00);
        const kd = number(pidInputs.linearKd, 0.10);
        const normalized = kp * distanceError + ki * pid.distanceIntegral - kd * closingTooFast;
        linearTarget = maxLinear * clamp(normalized, 0, 1) * gate;
      } else if (distanceError < -distanceDeadband * 1.5 && absOffset < 0.18) {
        const reverseProfile = smoothstep(distanceDeadband * 1.5, 0.14, -distanceError);
        linearTarget = -maxLinear * 0.18 * reverseProfile;
      }
    }

    const angularLimit = pid.curveInPlace
      ? maxAngular * 0.90
      : maxAngular * clamp(0.18 + smoothstep(offsetDeadband, 0.34, absOffset) * 0.82, 0, 1);
    angularTarget = clamp(angularTarget, -angularLimit, angularLimit);
    if (absOffset < 0.018 && Math.abs(lateralVelocity) < 0.22) angularTarget = 0;
    if (Math.abs(distanceError) < 0.009 && Math.abs(pid.sizeVelocity) < 0.10) linearTarget = 0;

    if (nowMs < pid.chillStopUntil) {
      pid.lastAngular = 0;
      pid.lastLinear = 0;
      setText(curveModeEl, 'chill stop');
      return { linear: 0, angular: 0, mode: 'anti-ondulazione stop' };
    }
    if (nowMs < pid.chillForwardUntil) {
      const settle = smoothstep(pid.chillStopUntil, pid.chillForwardUntil, nowMs);
      angularTarget *= 0.32;
      linearTarget = Math.max(linearTarget, maxLinear * (0.055 + settle * 0.070));
      linearTarget = Math.min(linearTarget, maxLinear * 0.14);
    }

    let angularStep;
    if (Math.sign(angularTarget) !== Math.sign(pid.lastAngular)) {
      angularStep = maxAngular * dt * 32.0;
    } else if (Math.abs(angularTarget) < Math.abs(pid.lastAngular)) {
      angularStep = maxAngular * dt * 34.0;
    } else {
      angularStep = maxAngular * dt * (pid.curveInPlace ? 18.0 : 14.0);
    }

    const linearAccel = maxLinear * dt * 7.5;
    const linearBrake = maxLinear * dt * 11.0;
    const linearStep = Math.abs(linearTarget) < Math.abs(pid.lastLinear) ? linearBrake : linearAccel;
    const angular = clamp(angularTarget, pid.lastAngular - angularStep, pid.lastAngular + angularStep);
    const linear = clamp(linearTarget, pid.lastLinear - linearStep, pid.lastLinear + linearStep);
    pid.lastAngular = angular;
    pid.lastLinear = linear;
    setText(curveModeEl, pid.curveInPlace ? 'attiva' : 'spenta');
    const mode = nowMs < pid.chillForwardUntil ? 'anti-ondulazione avanti chill' : (pid.curveInPlace ? 'curva sul posto' : gateMode(absOffset, Math.abs(angular)));
    return { linear, angular, mode };
  }

  function detectionPrecision(tag, cmd, perf) {
    const sizeRatio = Number(cmd.size_ratio);
    const offset = Math.abs(Number(cmd.offset_x));
    const detectMs = Number(perf.detect_ms);
    let score = 50;
    if (Number.isFinite(sizeRatio)) score += clamp(sizeRatio / 0.32, 0, 1) * 25;
    if (Number.isFinite(offset)) score += (1 - clamp(offset, 0, 1)) * 15;
    if (Number.isFinite(detectMs)) score += (1 - clamp(detectMs / 45, 0, 1)) * 10;
    if (tag?.corners?.length === 4) score += 5;
    score = Math.round(clamp(score, 0, 100));
    const label = score >= 82 ? 'alta' : score >= 62 ? 'media' : 'bassa';
    return `${label} (${score}%)`;
  }

  function updateStats(data, renderUi = true) {
    if (!data || !data.ok) {
      const msg = data?.error || 'errore rilevamento AprilTag';
      pid.missedFrames += 1;
      pid.backendErrorFrames += 1;
      pid.visibleFrames = 0;
      if (renderUi) {
        const suffix = followEnabled ? ` | retry ${pid.missedFrames}` : '';
        setText(targetStateEl, `${msg}${suffix}`);
        if (pid.backendErrorFrames > 8) {
          setText(precisionEl, 'camera instabile');
          setText(perfEl, 'retry camera');
        }
        if (pid.missedFrames % 3 === 1) drawGuide();
      }
      if (followEnabled && (pid.lastLinear !== 0 || pid.lastAngular !== 0)) {
        publishVelocity(0, 0, 'camera retry');
      }
      resetTransientTrackingState();
      return null;
    }
    const tag = data.tag || null;
    if (renderUi) drawTag(tag);
    if (!tag) {
      pid.missedFrames += 1;
      pid.visibleFrames = 0;
      if (renderUi) {
        const suffix = followEnabled ? ` | ricerca ${pid.missedFrames}` : '';
        setText(targetStateEl, selectedTagId === null ? `nessun AprilTag rilevato${suffix}` : `AprilTag ID ${selectedTagId} non visibile${suffix}`);
        setText(precisionEl, 'perso');
        setText(curveModeEl, 'spenta');
      }
      if (followEnabled && (pid.lastLinear !== 0 || pid.lastAngular !== 0)) {
        publishVelocity(0, 0, 'target perso');
      }
      resetTransientTrackingState();
      return null;
    }
    lastSeenAt = performance.now();
    pid.visibleFrames += 1;
    pid.missedFrames = 0;
    pid.backendErrorFrames = 0;
    const cmd = data.command || {};
    if (tag.dictionary) selectedTagDictionary = tag.dictionary;
    const tagSeenAt = performance.now();
    const tagDt = lastTagSampleAt ? clamp((tagSeenAt - lastTagSampleAt) / 1000, 0.006, 0.12) : 0.016;
    const prevCx = Number(lastTag?.cx);
    const prevCy = Number(lastTag?.cy);
    const vx = Number.isFinite(prevCx) ? clamp((Number(tag.cx) - prevCx) / tagDt, -4200, 4200) : 0;
    const vy = Number.isFinite(prevCy) ? clamp((Number(tag.cy) - prevCy) / tagDt, -4200, 4200) : 0;
    lastTagSampleAt = tagSeenAt;
    lastTag = {
      x: tag.x,
      y: tag.y,
      w: tag.w,
      h: tag.h,
      cx: tag.cx,
      cy: tag.cy,
      side: tag.side,
      vx,
      vy,
    };
    if (renderUi) {
      if (selectedTagId !== null) setText(tagIdEl, selectedTagId);
      setText(targetStateEl, `rilevato AprilTag ID ${tag.id}${tag.dictionary ? ` | ${tag.dictionary}` : ''}`);
      setText(offsetEl, cmd.offset_x ?? '-');
      setText(ratioEl, cmd.size_ratio ?? '-');
      const perf = data.perf || {};
      const actualHz = pid.lastLoopMs ? `${(1000 / pid.lastLoopMs).toFixed(1)} Hz` : '-';
      setText(precisionEl, detectionPrecision(tag, cmd, perf));
      setText(perfEl, `det ${perf.detect_ms ?? '-'} ms | frame ${perf.frame_age_ms ?? '-'} ms | loop ${actualHz}`);
    }
    return cmd;
  }

  function currentPayload(acquireTarget) {
    const streamUrl = (streamUrlInput?.value || '').trim();
    const streamIsVideo = /^(rtsp|rtmp):\/\//i.test(streamUrl) || /\/(stream|mjpeg|mjpg|video|video_feed)\b/i.test(streamUrl) || /[?&]action=stream/i.test(streamUrl);
    const baseDetectWidth = Math.max(0, Math.min(1920, number(detectWidthInput, 360)));
    const lastSide = Number(lastTag?.side || 0);
    const needsFarSearch = pid.missedFrames > 0 || (lastSide > 0 && lastSide < 42);
    const adaptiveDetectWidth = acquireTarget
      ? Math.max(baseDetectWidth, 640)
      : (needsFarSearch ? Math.max(baseDetectWidth, 900) : baseDetectWidth);
    return {
      stream_url: streamUrl,
      snapshot_url: streamIsVideo ? '' : (snapshotUrlInput?.value || '').trim(),
      acquire_target: !!acquireTarget,
      target_id: selectedTagId,
      target_dictionary: selectedTagDictionary || 'APRILTAG_25h9',
      missed_frames: pid.missedFrames,
      last_tag: lastTag,
      desired_size_ratio: number(desiredRatioInput, 18) / 100,
      max_linear: number(maxLinearInput, 0.18),
      max_angular: number(maxAngularInput, 0.45),
      max_detect_width: Math.min(1920, adaptiveDetectWidth),
      far_search: needsFarSearch,
      last_frame_id: lastBackendFrameId,
    };
  }

  function updateProfiler(data, fetchMs = 0) {
    const perf = data?.perf || {};
    const loopHz = pid.lastLoopMs ? `${(1000 / pid.lastLoopMs).toFixed(1)} Hz` : '-';
    setText(profilerLoopEl, loopHz);
    setText(profilerFetchEl, fetchMs ? `${fetchMs.toFixed(1)} ms` : '-');
    const detectText = perf.total_ms !== undefined
      ? `${perf.detect_ms ?? '-'} / ${perf.total_ms} ms`
      : (perf.detect_ms !== undefined ? `${perf.detect_ms} ms` : '-');
    setText(profilerDetectEl, detectText);
    setText(profilerFrameEl, perf.frame_age_ms !== undefined ? `${perf.frame_age_ms} ms` : '-');
    setText(profilerMissEl, `${pid.missedFrames}`);
    const roiSuffix = data?.far_search ? ' FAR' : (data?.fast_roi ? ' ROI' : '');
    setText(profilerDictEl, `${selectedTagDictionary || data?.tag?.dictionary || '-'}${roiSuffix}`);

    // Update Graphical progress bars
    // 1. Loop latency bar (scaled relative to max loop rate)
    const hz = pid.lastLoopMs ? (1000 / pid.lastLoopMs) : 0;
    const targetHz = number(pidHzInput, 160);
    const hzMax = Math.max(120, targetHz);
    const hzPercent = clamp((hz / hzMax) * 100, 0, 100);
    if (barLoopEl) {
      barLoopEl.style.width = `${hzPercent}%`;
      if (hz >= targetHz * 0.9) {
        setBarColorClass(barLoopEl, 'success');
      } else if (hz >= targetHz * 0.6) {
        setBarColorClass(barLoopEl, 'warning');
      } else {
        setBarColorClass(barLoopEl, 'danger');
      }
    }

    // 2. Fetch latency bar (scaled 0-50ms)
    if (barFetchEl) {
      const fetchPercent = clamp((fetchMs / 50) * 100, 0, 100);
      barFetchEl.style.width = `${fetchPercent}%`;
      if (fetchMs === 0) {
        barFetchEl.style.width = '0%';
      } else if (fetchMs <= 15) {
        setBarColorClass(barFetchEl, 'info');
      } else if (fetchMs <= 35) {
        setBarColorClass(barFetchEl, 'warning');
      } else {
        setBarColorClass(barFetchEl, 'danger');
      }
    }

    // 3. Detection / Total overlapping bars (scaled 0-60ms)
    if (barDetectEl && barTotalEl) {
      const detectMs = perf.detect_ms ?? 0;
      const totalMs = perf.total_ms ?? detectMs;
      const detectPercent = clamp((detectMs / 60) * 100, 0, 100);
      const totalPercent = clamp((totalMs / 60) * 100, 0, 100);
      barDetectEl.style.width = `${detectPercent}%`;
      barTotalEl.style.width = `${totalPercent}%`;

      if (totalMs === 0) {
        barDetectEl.style.width = '0%';
        barTotalEl.style.width = '0%';
      } else if (totalMs > 45) {
        setBarColorClass(barTotalEl, 'danger');
      } else if (totalMs > 25) {
        setBarColorClass(barTotalEl, 'warning');
      } else {
        setBarColorClass(barTotalEl, 'secondary');
      }
    }

    // 4. Frame Age bar (scaled 0-150ms)
    if (barFrameEl) {
      const age = perf.frame_age_ms ?? 0;
      const agePercent = clamp(age / 150 * 100, 0, 100);
      barFrameEl.style.width = `${agePercent}%`;
      if (perf.frame_age_ms === undefined) {
        barFrameEl.style.width = '0%';
      } else if (age <= 60) {
        setBarColorClass(barFrameEl, 'success');
      } else if (age <= 120) {
        setBarColorClass(barFrameEl, 'warning');
      } else {
        setBarColorClass(barFrameEl, 'danger');
      }
    }

    // 5. Stability bar (Math.max(0, 100 - missedFrames * 20) %)
    const stability = Math.max(0, 100 - pid.missedFrames * 20);
    setText(profilerStabilityValEl, `${stability}%`);
    if (barStabilityEl) {
      barStabilityEl.style.width = `${stability}%`;
      if (stability >= 90) {
        setBarColorClass(barStabilityEl, 'success');
      } else if (stability >= 50) {
        setBarColorClass(barStabilityEl, 'warning');
      } else {
        setBarColorClass(barStabilityEl, 'danger');
      }
    }

    // 6. Target configuration static/active indicator
    if (barDictEl) {
      barDictEl.style.width = '100%';
      if (selectedTagId !== null) {
        setBarColorClass(barDictEl, 'success');
      } else {
        setBarColorClass(barDictEl, 'muted');
      }
    }
  }

  async function tagStep(acquireTarget) {
    if (busy) return null;
    busy = true;
    let timeout = null;
    const now = performance.now();
    const renderUi = acquireTarget || now - lastUiAt > 140;
    if (renderUi) lastUiAt = now;
    try {
      const fetchStarted = performance.now();
      const controller = new AbortController();
      timeout = window.setTimeout(() => controller.abort(), acquireTarget ? 520 : 190);
      const res = await fetch('/api/tracking/apriltag-step', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(currentPayload(acquireTarget)),
        signal: controller.signal,
      });
      window.clearTimeout(timeout);
      timeout = null;
      const text = await res.text();
      const fetchMs = performance.now() - fetchStarted;
      let data;
      try { data = JSON.parse(text); } catch (_) { data = { ok: false, error: text || `HTTP ${res.status}` }; }
      if (data.ok && data.acquired_tag_id !== undefined && data.acquired_tag_id !== null) {
        selectedTagId = data.acquired_tag_id;
        selectedTagDictionary = data.acquired_tag_dictionary || data.tag?.dictionary || selectedTagDictionary;
        setText(tagIdEl, selectedTagId);
      }
      if (data.perf?.frame_id) lastBackendFrameId = Number(data.perf.frame_id) || lastBackendFrameId;
      const cmd = updateStats(data, renderUi);
      updateProfiler(data, fetchMs);
      return cmd;
    } catch (err) {
      const data = { ok: false, error: err?.name === 'AbortError' ? 'timeout camera, retry' : `errore backend: ${err}` };
      updateStats(data, renderUi);
      updateProfiler(data, 0);
      return null;
    } finally {
      if (timeout) window.clearTimeout(timeout);
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
      setText(streamStateEl, 'RTSP usato solo dal detector; preview browser non disponibile');
      drawGuide();
      return;
    }
    const isMjpeg = /\/(stream|mjpeg|mjpg|video|video_feed)\b/i.test(url) || /[?&]action=stream/i.test(url);
    streamImg.onerror = () => setText(streamStateEl, isMjpeg ? 'errore stream camera' : 'errore snapshot');
    streamImg.onload = () => {
      setText(streamStateEl, isMjpeg ? 'stream diretto live' : 'snapshot connesso');
      drawGuide();
    };
    streamImg.src = isMjpeg ? url : `${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`;
    setTimeout(drawGuide, 600);
  }

  async function detectTag() {
    followEnabled = false;
    window.clearTimeout(followTimer);
    selectedTagId = null;
    selectedTagDictionary = '';
    lastTag = null;
    lastTagSampleAt = 0;
    lastBackendFrameId = 0;
    pid = resetPid();
    setText(tagIdEl, '-');
    setText(followStateEl, 'riconoscimento AprilTag...');
    await tagStep(true);
  }

  async function startFollow() {
    if (selectedTagId === null) {
      setText(followStateEl, 'acquisizione automatica AprilTag...');
      await tagStep(true);
      if (selectedTagId === null) {
        setText(followStateEl, 'nessun AprilTag agganciato');
        drawGuide();
        return;
      }
    }
    const ok = await ensureRosBridge();
    if (!ok) {
      setText(followStateEl, 'ROSBridge non pronto');
      return;
    }
    followEnabled = true;
    pid = resetPid();
    lastBackendFrameId = 0;
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
      const sent = publishVelocity(out.linear, out.angular, out.mode);
      if (!sent && performance.now() > rosReconnectAt) {
        rosReconnectAt = performance.now() + 1200;
        ensureRosBridge();
      }
    } else if (followEnabled && (!socket || socket.readyState !== WebSocket.OPEN || !joystickToken) && performance.now() > rosReconnectAt) {
      rosReconnectAt = performance.now() + 1200;
      ensureRosBridge();
    }
    const elapsed = performance.now() - started;
    const targetMs = 1000 / Math.max(1, Math.min(180, number(pidHzInput, 160)));
    followTimer = window.setTimeout(followLoop, Math.max(0, targetMs - elapsed));
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
    selectedTagDictionary = '';
    lastTag = null;
    lastTagSampleAt = 0;
    pid = resetPid();
    busy = false;
    advertised = false;
    joystickToken = '';
    lastBackendFrameId = 0;
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
    selectedTagDictionary = '';
    lastTag = null;
    lastTagSampleAt = 0;
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
