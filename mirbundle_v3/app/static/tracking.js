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
  const streamUrlInput = $('streamUrlInput');
  const snapshotUrlInput = $('snapshotUrlInput');
  const streamStateEl = $('trackingStreamState');
  const targetStateEl = $('trackingTargetState');
  const tagIdEl = $('trackingTagId');
  const offsetEl = $('trackingOffset');
  const ratioEl = $('trackingRatio');
  const suggestionEl = $('trackingSuggestion');
  const commandEl = $('trackingCommand');
  const followStateEl = $('trackingFollowState');
  const desiredRatioInput = $('trackingTargetSize');
  const desiredRatioValue = $('trackingTargetSizeValue');
  const maxLinearInput = $('trackingLinearGain');
  const maxAngularInput = $('trackingAngularGain');

  let selectedTagId = null;
  let lastTag = null;
  let followEnabled = false;
  let followTimer = null;
  let followDelayMs = 180;
  let busy = false;

  function setText(el, value) { if (el) el.textContent = value; }

  function updateTargetSizeLabel() {
    if (desiredRatioValue && desiredRatioInput) desiredRatioValue.textContent = `${desiredRatioInput.value}%`;
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

  function guideRect() {
    const w = overlay.width;
    const h = overlay.height;
    return {
      x: Math.round(w * 0.30),
      y: Math.round(h * 0.22),
      w: Math.round(w * 0.40),
      h: Math.round(h * 0.50),
    };
  }

  function drawGuide() {
    if (!ensureCanvas()) return;
    const g = guideRect();
    ctx.strokeStyle = 'rgba(59,130,246,0.95)';
    ctx.lineWidth = 4;
    ctx.setLineDash([14, 8]);
    ctx.strokeRect(g.x, g.y, g.w, g.h);
    ctx.setLineDash([]);
    ctx.fillStyle = 'rgba(59,130,246,0.14)';
    ctx.fillRect(g.x, g.y, g.w, g.h);
    ctx.fillStyle = '#93c5fd';
    ctx.font = '20px sans-serif';
    ctx.fillText('INQUADRA QUI L\'APRILTAG', g.x + 10, Math.max(24, g.y - 10));
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
    if (pts.length === 4) {
      ctx.strokeStyle = '#22c55e';
      ctx.lineWidth = 5;
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      ctx.closePath();
      ctx.stroke();
    } else {
      ctx.strokeStyle = '#22c55e';
      ctx.lineWidth = 5;
      ctx.strokeRect(tag.x, tag.y, tag.w, tag.h);
    }
    ctx.fillStyle = 'rgba(34,197,94,0.18)';
    ctx.fillRect(tag.x, tag.y, tag.w, tag.h);
    ctx.fillStyle = '#22c55e';
    ctx.font = '22px sans-serif';
    ctx.fillText(`APRILTAG ID ${tag.id}`, tag.x + 6, Math.max(26, tag.y - 8));
  }

  function updateStats(data) {
    if (!data || !data.ok) {
      const msg = (data && data.error) ? data.error : 'errore rilevamento';
      setText(targetStateEl, msg);
      setText(suggestionEl, 'stop');
      setText(commandEl, 'linear=0 angular=0');
      drawGuide();
      return;
    }
    lastTag = data.tag || null;
    drawTag(lastTag);
    if (!lastTag) {
      setText(targetStateEl, selectedTagId === null ? 'nessun AprilTag rilevato: mettilo nel riquadro blu' : `target ID ${selectedTagId} non visibile`);
    } else {
      setText(targetStateEl, `rilevato AprilTag ID ${lastTag.id}`);
    }
    if (selectedTagId !== null) setText(tagIdEl, selectedTagId);
    const cmd = data.command || {};
    setText(offsetEl, cmd.offset_x ?? '-');
    setText(ratioEl, cmd.size_ratio ?? '-');
    setText(suggestionEl, cmd.suggestion || 'stop');
    const mf = data.mission_follow || {};
    if (mf.action) {
      const missionLabel = mf.mission_name ? `${mf.action} → ${mf.mission_name}` : mf.action;
      setText(commandEl, missionLabel);
    } else {
      setText(commandEl, cmd.suggestion || 'stop');
    }
    if (data.drive_warning) setText(followStateEl, data.drive_warning);
    else if (followEnabled && data.drive_sent) setText(followStateEl, mf.mission_name ? `micro-missione inviata: ${mf.mission_name}` : 'follow attivo: queue/stop inviato');
    else setText(followStateEl, followEnabled ? 'follow attivo: in attesa intervallo/isteresi micro-missione' : 'spento');
  }

  function currentPayload(sendFollow, acquireTarget) {
    return {
      stream_url: (streamUrlInput?.value || '').trim(),
      snapshot_url: (snapshotUrlInput?.value || '').trim(),
      follow_enabled: !!sendFollow,
      acquire_target: !!acquireTarget,
      target_id: selectedTagId,
      desired_size_ratio: Number(desiredRatioInput?.value || 18) / 100.0,
      max_linear: Number(maxLinearInput?.value || 0.10),
      max_angular: Number(maxAngularInput?.value || 0.30),
    };
  }

  async function tagStep(sendFollow, acquireTarget) {
    if (busy) return;
    busy = true;
    try {
      const res = await fetch('/api/tracking/apriltag-step', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(currentPayload(sendFollow, acquireTarget)),
      });
      const text = await res.text();
      let data;
      try { data = JSON.parse(text); } catch (_) { data = { ok: false, error: text || `HTTP ${res.status}` }; }
      if (data.ok && data.acquired_tag_id !== undefined && data.acquired_tag_id !== null) {
        selectedTagId = data.acquired_tag_id;
        setText(tagIdEl, selectedTagId);
      }
      updateStats(data);
      return data;
    } catch (err) {
      const msg = `errore chiamata backend: ${err}`;
      setText(targetStateEl, msg);
      setText(followStateEl, msg);
      drawGuide();
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

  // RTSP/RTMP: nessuna anteprima browser, usa solo il backend
  if (/^(rtsp|rtmp):\/\//i.test(url)) {
    setText(streamStateEl, 'stream RTSP/RTMP usato dal backend; anteprima browser non disponibile');
    drawGuide();
    return;
  }

  if (!streamImg) return;

  // Determina se è uno stream MJPEG continuo o uno snapshot statico
  const isMjpeg =
    /\/(stream|mjpeg|mjpg|video|video_feed)\b/i.test(url) ||
    /[?&]action=stream/i.test(url);

  setText(streamStateEl, 'connessione stream...');

  if (isMjpeg) {
    // Per MJPEG il browser gestisce il multipart in modo nativo tramite <img>.
    // NON aggiungere cache-busting: spezzerebbe il flusso multipart.
    streamImg.onerror = () => { setText(streamStateEl, 'errore stream MJPEG'); };
    streamImg.onload = () => {
      setText(streamStateEl, 'stream MJPEG live connesso');
      drawGuide();
    };
    streamImg.src = url;
  } else {
    // Snapshot statico (es. /snapshot.jpg): aggiunge cache-busting per forzare aggiornamento
    streamImg.onerror = () => { setText(streamStateEl, 'errore snapshot'); };
    streamImg.onload = () => {
      setText(streamStateEl, 'snapshot connesso');
      drawGuide();
    };
    streamImg.src = `${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`;
  }

  setTimeout(drawGuide, 600);
}

  async function detectTag() {
    followEnabled = false;
    if (followTimer) clearTimeout(followTimer);
    followTimer = null;
    selectedTagId = null;
    setText(followStateEl, 'riconoscimento AprilTag in corso...');
    setText(targetStateEl, 'mettiti nel riquadro blu con il tag ben visibile...');
    drawGuide();
    await tagStep(false, true);
  }

  function startFollow() {
    if (selectedTagId === null) {
      setText(followStateEl, 'prima premi “Riconosci AprilTag” e acquisisci il target');
      drawGuide();
      return;
    }
    followEnabled = true;
    setText(followStateEl, `follow avviato su AprilTag ID ${selectedTagId}`);
    if (followTimer) clearTimeout(followTimer);
    followLoop();
  }

  async function followLoop() {
    if (!followEnabled) return;
    const started = performance.now();
    await tagStep(true, false).catch(() => {});
    if (!followEnabled) return;
    const elapsed = performance.now() - started;
    followTimer = setTimeout(followLoop, Math.max(80, followDelayMs - elapsed));
  }

  async function stopFollow() {
    followEnabled = false;
    if (followTimer) clearTimeout(followTimer);
    followTimer = null;
    setText(followStateEl, 'arresto follow...');
    try {
      const res = await fetch('/api/tracking/stop', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      setText(followStateEl, data.ok ? 'spento: queue pulita / follow_stop inviato' : (data.error || 'spento')); 
    } catch (err) {
      setText(followStateEl, `spento, errore stop: ${err}`);
    }
  }

  clearBtn?.addEventListener('click', () => {
    selectedTagId = null;
    lastTag = null;
    setText(tagIdEl, '-');
    setText(targetStateEl, 'target rimosso');
    drawGuide();
  });
  connectBtn?.addEventListener('click', connectStream);
  detectBtn?.addEventListener('click', detectTag);
  startFollowBtn?.addEventListener('click', startFollow);
  stopFollowBtn?.addEventListener('click', stopFollow);
  desiredRatioInput?.addEventListener('input', updateTargetSizeLabel);
  updateTargetSizeLabel();
  setText(followStateEl, 'pronto');
  drawGuide();
})();
