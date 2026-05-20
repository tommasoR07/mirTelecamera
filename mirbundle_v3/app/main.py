from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
from typing import Any
import asyncio
import threading
import time

import httpx
from fastapi import Body

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .services import get_client

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title='MiR Tracking System')
app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'static')), name='static')
templates = Jinja2Templates(directory=str(BASE_DIR / 'templates'))
_SNAPSHOT_HTTP_CLIENT: httpx.AsyncClient | None = None


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def qerr(exc: Exception) -> str:
    return quote(str(exc), safe='')


def render(request: Request, template: str, **context):
    base_context = {
        'request': request,
        'settings': db.get_settings(),
        'message': request.query_params.get('message', ''),
        'error': request.query_params.get('error', ''),
    }
    base_context.update(context)
    return templates.TemplateResponse(template, base_context)


@app.on_event('startup')
def on_startup() -> None:
    db.init_db()


@app.on_event('shutdown')
async def on_shutdown() -> None:
    global _SNAPSHOT_HTTP_CLIENT
    _STREAM_FRAME_CACHE.stop()
    if _SNAPSHOT_HTTP_CLIENT is not None:
        await _SNAPSHOT_HTTP_CLIENT.aclose()
        _SNAPSHOT_HTTP_CLIENT = None


@app.get('/', response_class=HTMLResponse)
async def home(request: Request):
    status = None
    queue = []
    error = ''
    try:
        client = get_client()
        status = await client.get_status()
        queue = await client.get_queue()
    except Exception as exc:
        error = str(exc)
    return render(request, 'dashboard.html', status=status, queue=queue, page_error=error)


@app.get('/settings', response_class=HTMLResponse)
def settings_page(request: Request):
    return render(request, 'settings.html')


@app.post('/settings')
def save_settings(
    host: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    mission_group_id: str = Form(''),
    drive_endpoint_path: str = Form(''),
    drive_http_method: str = Form('POST'),
    drive_body_template: str = Form('{"linear": {{linear}}, "angular": {{angular}}}'),
    camera_stream_url: str = Form(''),
    camera_snapshot_url: str = Form(''),
    manual_take_endpoint_path: str = Form(''),
    manual_take_http_method: str = Form('PUT'),
    manual_take_body_template: str = Form('{"mode_id": 1}'),
    manual_release_endpoint_path: str = Form(''),
    manual_release_http_method: str = Form('PUT'),
    manual_release_body_template: str = Form('{"state_id": 3}'),
    follow_forward_mission: str = Form('follow_avanti'),
    follow_left_mission: str = Form('follow_sinistra'),
    follow_right_mission: str = Form('follow_destra'),
    follow_stop_mission: str = Form('follow_stop'),
    follow_min_interval_seconds: str = Form('0.45'),
):
    db.save_settings(
        host,
        username,
        password,
        mission_group_id,
        drive_endpoint_path,
        drive_http_method,
        drive_body_template,
        camera_stream_url,
        camera_snapshot_url,
        manual_take_endpoint_path,
        manual_take_http_method,
        manual_take_body_template,
        manual_release_endpoint_path,
        manual_release_http_method,
        manual_release_body_template,
        follow_forward_mission,
        follow_left_mission,
        follow_right_mission,
        follow_stop_mission,
        follow_min_interval_seconds,
    )
    return redirect('/settings?message=Configurazione salvata')


@app.post('/robot/clear-errors')
async def clear_errors():
    try:
        client = get_client()
        await client.clear_errors()
        return redirect('/?message=Reset errori inviato')
    except Exception as exc:
        return redirect(f'/?error={qerr(exc)}')


@app.post('/robot/ready')
async def robot_ready():
    try:
        client = get_client()
        await client.resume_ready()
        return redirect('/?message=Comando Ready/Resume inviato')
    except Exception as exc:
        return redirect(f'/?error={qerr(exc)}')


@app.get('/missions', response_class=HTMLResponse)
async def missions_page(request: Request, group_id: str = ''):
    groups = []
    missions = []
    queue = []
    error = ''
    selected_group = group_id or db.get_settings().get('mission_group_id', '')
    try:
        client = get_client()
        groups = await client.get_mission_groups()
        if not selected_group and groups:
            selected_group = groups[0].get('guid', '')
        if selected_group:
            missions = await client.get_missions(selected_group)
        queue = await client.get_queue()
    except Exception as exc:
        error = str(exc)
    return render(request, 'missions.html', groups=groups, missions=missions, queue=queue, selected_group=selected_group, page_error=error)


@app.post('/missions/enqueue')
async def enqueue_mission(
    mission_id: str = Form(...),
    parameter_input_name: str = Form(''),
    parameter_value: str = Form(''),
    group_id: str = Form(''),
):
    suffix = f'&group_id={group_id}' if group_id else ''
    try:
        client = get_client()
        await client.enqueue_mission(mission_id, parameter_input_name, parameter_value)
        return redirect(f'/missions?message=Missione accodata{suffix}')
    except Exception as exc:
        return redirect(f'/missions?error={qerr(exc)}{suffix}')


@app.post('/missions/start-now')
async def start_mission_now(
    mission_id: str = Form(...),
    parameter_input_name: str = Form(''),
    parameter_value: str = Form(''),
    group_id: str = Form(''),
):
    suffix = f'&group_id={group_id}' if group_id else ''
    try:
        client = get_client()
        try:
            await client.clear_errors()
        except Exception:
            pass
        try:
            await client.resume_ready()
        except Exception:
            pass
        try:
            await client.clear_queue()
        except Exception:
            pass
        result = await client.enqueue_mission(mission_id, parameter_input_name, parameter_value)
        msg = quote(str(result)[:300], safe='')
        return redirect(f'/missions?message=Missione avviata/accodata. Risposta MiR: {msg}{suffix}')
    except Exception as exc:
        return redirect(f'/missions?error={qerr(exc)}{suffix}')


@app.post('/missions/resume')
async def resume_robot_from_missions(group_id: str = Form('')):
    suffix = f'&group_id={group_id}' if group_id else ''
    try:
        client = get_client()
        await client.resume_ready()
        return redirect(f'/missions?message=Comando Resume/Ready inviato{suffix}')
    except Exception as exc:
        return redirect(f'/missions?error={qerr(exc)}{suffix}')


@app.post('/missions/clear-queue')
async def clear_queue(group_id: str = Form('')):
    suffix = f'&group_id={group_id}' if group_id else ''
    try:
        client = get_client()
        await client.clear_queue()
        return redirect(f'/missions?message=Coda missioni pulita{suffix}')
    except Exception as exc:
        return redirect(f'/missions?error={qerr(exc)}{suffix}')


@app.get('/maps', response_class=HTMLResponse)
async def maps_page(request: Request, map_id: str = ''):
    maps = []
    positions = []
    zones = []
    error = ''
    selected_map = map_id
    try:
        client = get_client()
        maps = await client.get_maps()
        if not selected_map and maps:
            selected_map = maps[0].get('guid', '')
        if selected_map:
            positions = await client.get_map_positions(selected_map)
            zones = await client.get_map_zones(selected_map)
    except Exception as exc:
        error = str(exc)
    return render(request, 'maps.html', maps=maps, positions=positions, zones=zones, selected_map=selected_map, page_error=error)


@app.post('/maps/set-active')
async def set_active_map(map_id: str = Form(...)):
    try:
        client = get_client()
        result = await client.set_active_map(map_id)
        msg = quote(str(result)[:300], safe='')
        return redirect(f'/maps?map_id={map_id}&message=Mappa attiva impostata. Risposta MiR: {msg}')
    except Exception as exc:
        return redirect(f'/maps?map_id={map_id}&error={qerr(exc)}')


@app.post('/maps/localize')
async def localize_robot(map_id: str = Form(...), position_id: str = Form(...)):
    try:
        client = get_client()
        result = await client.localize_to_position(map_id, position_id)
        msg = quote(str(result.get('payload', result))[:300], safe='')
        return redirect(f'/maps?map_id={map_id}&message=Comando localizzazione inviato. Payload: {msg}')
    except Exception as exc:
        return redirect(f'/maps?map_id={map_id}&error={qerr(exc)}')


@app.get('/tracking', response_class=HTMLResponse)
def tracking_page(request: Request):
    settings = db.get_settings()
    return render(
        request,
        'tracking.html',
        camera_stream_url=settings.get('camera_stream_url', ''),
        camera_snapshot_url=settings.get('camera_snapshot_url', ''),
    )




@app.get('/joystick-lab', response_class=HTMLResponse)
def joystick_lab_page(request: Request):
    return render(request, 'joystick_lab.html')


# Stato leggero in memoria per rendere il follow più fluido.
# Evita di accodare micro-missioni diverse a ogni frame quando l'AprilTag vibra
# vicino alla soglia sinistra/destra/avanti.
_FOLLOW_STATE: dict[str, Any] = {
    'sent_action': None,
    'sent_ts': 0.0,
    'pending_action': None,
    'pending_count': 0,
    'last_seen_ts': 0.0,
}


def _norm_name(value: str) -> str:
    return (value or '').strip().lower().replace(' ', '_')


async def _find_mission_id_by_name_or_guid(client, name_or_guid: str) -> tuple[str, str]:
    wanted = (name_or_guid or '').strip()
    if not wanted:
        raise RuntimeError('Missione follow non configurata in Settings.')
    # Se l'utente inserisce già un GUID, prova a usarlo direttamente; altrimenti cerca per nome.
    missions = await client.get_all_missions()
    wanted_norm = _norm_name(wanted)
    for mission in missions:
        if str(mission.get('guid', '')).strip() == wanted:
            return str(mission.get('guid')), str(mission.get('name') or wanted)
    for mission in missions:
        if _norm_name(str(mission.get('name', ''))) == wanted_norm:
            return str(mission.get('guid')), str(mission.get('name') or wanted)
    available = ', '.join(str(m.get('name', '?')) for m in missions[:30])
    raise RuntimeError(f'Missione follow non trovata: {wanted}. Missioni disponibili: {available}')


def _decide_follow_mission(command: dict[str, Any], desired_size_ratio: float) -> str:
    """Decisione più stabile per micro-missioni.

    Restituisce: avanti, sinistra, destra, stop oppure none.
    - none = non inviare nulla, mantieni la situazione corrente.
    - usa isteresi: una correzione laterale parte solo se il tag è davvero decentrato.
    - stop viene usato solo quando la distanza è corretta o il tag è perso da un po'.
    """
    offset = command.get('offset_x')
    size_ratio = command.get('size_ratio')
    now = time.time()

    if offset is None or size_ratio is None:
        # Non fermare al primo frame perso: lo snapshot può saltare per un istante.
        last_seen = float(_FOLLOW_STATE.get('last_seen_ts') or 0.0)
        if now - last_seen < 0.7:
            return 'none'
        return 'stop'

    _FOLLOW_STATE['last_seen_ts'] = now
    offset = float(offset)
    size_ratio = float(size_ratio)
    abs_offset = abs(offset)

    # Isteresi laterale: soglia alta per iniziare, soglia bassa per smettere.
    previous = _FOLLOW_STATE.get('sent_action')
    lateral_enter = 0.24
    lateral_exit = 0.12

    if previous == 'sinistra' and offset < -lateral_exit:
        return 'sinistra'
    if previous == 'destra' and offset > lateral_exit:
        return 'destra'

    if offset > lateral_enter:
        return 'destra'
    if offset < -lateral_enter:
        return 'sinistra'

    # Avanza solo se il tag è abbastanza centrato: evita avanti+curve a scatti.
    too_far_margin = 0.035
    if abs_offset < 0.30 and size_ratio < max(0.05, desired_size_ratio - too_far_margin):
        return 'avanti'

    # Se è quasi centrato e alla distanza giusta, stop morbido.
    if abs_offset < 0.18 and size_ratio >= desired_size_ratio - too_far_margin:
        return 'stop'

    # Zona morta: non cambiare comando.
    return 'none'


async def _run_follow_micro_mission(action: str, settings: dict[str, Any]) -> dict[str, Any]:
    client = get_client()
    if action == 'none':
        return {'sent': False, 'action': action, 'reason': 'zona morta: nessuna nuova micro-missione'}

    action_to_setting = {
        'avanti': 'follow_forward_mission',
        'sinistra': 'follow_left_mission',
        'destra': 'follow_right_mission',
        'stop': 'follow_stop_mission',
    }
    mission_setting = action_to_setting.get(action, 'follow_stop_mission')
    mission_name_or_guid = str(settings.get(mission_setting, '')).strip()
    now = time.time()
    try:
        interval = float(settings.get('follow_min_interval_seconds') or 0.45)
    except Exception:
        interval = 0.45
    interval = max(0.25, min(interval, 0.8))

    # Debounce: la stessa decisione deve comparire per più frame prima di inviare.
    # Rende il robot più fluido perché evita sinistra/destra/stop dovuti a jitter.
    stable_required = 1
    if _FOLLOW_STATE.get('pending_action') == action:
        _FOLLOW_STATE['pending_count'] = int(_FOLLOW_STATE.get('pending_count') or 0) + 1
    else:
        _FOLLOW_STATE['pending_action'] = action
        _FOLLOW_STATE['pending_count'] = 1

    if int(_FOLLOW_STATE.get('pending_count') or 0) < stable_required:
        return {'sent': False, 'action': action, 'reason': f'attendo conferma comando stabile ({_FOLLOW_STATE["pending_count"]}/{stable_required})'}

    # Non riaccodare sempre la stessa missione.
    if _FOLLOW_STATE.get('sent_action') == action and now - float(_FOLLOW_STATE.get('sent_ts') or 0.0) < interval:
        return {'sent': False, 'action': action, 'reason': f'attesa intervallo {interval}s'}

    # Se ho già mandato stop da poco, non continuare a pulire la queue.
    if action == 'stop' and _FOLLOW_STATE.get('sent_action') == 'stop' and now - float(_FOLLOW_STATE.get('sent_ts') or 0.0) < max(interval, 2.0):
        return {'sent': False, 'action': action, 'reason': 'stop già inviato'}

    try:
        await client.clear_queue()
    except Exception:
        pass

    if action == 'stop' and not mission_name_or_guid:
        _FOLLOW_STATE.update({'sent_action': action, 'sent_ts': now})
        return {'sent': True, 'action': action, 'mission': None, 'result': 'queue cancellata'}

    mission_id, mission_name = await _find_mission_id_by_name_or_guid(client, mission_name_or_guid)
    result = await client.enqueue_mission(mission_id)
    _FOLLOW_STATE.update({'sent_action': action, 'sent_ts': now})
    return {'sent': True, 'action': action, 'mission_id': mission_id, 'mission_name': mission_name, 'result': result}


# ---------------- Tracking AprilTag + follow ----------------
def _load_cv2():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        return cv2, np
    except Exception as exc:  # pragma: no cover
        raise RuntimeError('OpenCV non installato. Esegui: pip install -r requirements.txt') from exc


def _is_video_stream_url(url: str) -> bool:
    clean = (url or '').strip().lower()
    path = clean.split('?', 1)[0].rstrip('/')
    return (
        clean.startswith(('rtsp://', 'rtmp://'))
        or path.endswith(('/video', '/video_feed', '/stream', '/mjpeg', '/mjpg'))
        or 'action=stream' in clean
    )


class _StreamFrameCache:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.url = ''
        self.capture = None
        self.thread: threading.Thread | None = None
        self.running = False
        self.jpeg: bytes | None = None
        self.last_frame_ts = 0.0
        self.last_error = ''

    def get_jpeg(self, url: str, wait_seconds: float = 2.0) -> bytes:
        url = (url or '').strip()
        if not url:
            raise RuntimeError('Stream URL camera non configurato.')
        self._ensure_running(url)
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            with self.lock:
                if self.url == url and self.jpeg:
                    return self.jpeg
                last_error = self.last_error
            time.sleep(0.03)
        raise RuntimeError(last_error or f'Nessun frame disponibile dallo stream: {url}')

    def _ensure_running(self, url: str) -> None:
        with self.lock:
            stale = bool(self.running and self.url == url and self.last_frame_ts and time.time() - self.last_frame_ts > 3.0)
            failed = bool(self.running and self.url == url and not self.jpeg and self.last_error)
            if self.running and self.url == url and not stale and not failed:
                return
            self._stop_locked()
            self.url = url
            self.jpeg = None
            self.last_error = ''
            self.last_frame_ts = 0.0
            self.running = True
            self.thread = threading.Thread(target=self._loop, args=(url,), daemon=True)
            self.thread.start()

    def _stop_locked(self) -> None:
        self.running = False
        capture = self.capture
        self.capture = None
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass

    def stop(self) -> None:
        with self.lock:
            self._stop_locked()

    def _loop(self, url: str) -> None:
        cv2, _ = _load_cv2()
        cap = cv2.VideoCapture(url)
        with self.lock:
            if self.url == url and self.running:
                self.capture = cap
        try:
            if hasattr(cv2, 'CAP_PROP_BUFFERSIZE'):
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                with self.lock:
                    self.last_error = f'Stream video non raggiungibile: {url}'
                return
            while True:
                with self.lock:
                    if not self.running or self.url != url:
                        return
                ok, frame = cap.read()
                if not ok or frame is None:
                    with self.lock:
                        self.last_error = f'Impossibile leggere un frame dallo stream: {url}'
                    time.sleep(0.08)
                    continue
                encoded, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                if encoded:
                    with self.lock:
                        if self.running and self.url == url:
                            self.jpeg = buffer.tobytes()
                            self.last_frame_ts = time.time()
                            self.last_error = ''
        finally:
            with self.lock:
                if self.capture is cap:
                    self.capture = None
                if self.url == url:
                    self.running = False
            cap.release()


_STREAM_FRAME_CACHE = _StreamFrameCache()
_APRILTAG_DETECTORS: dict[str, Any] = {}


def _get_snapshot_http_client() -> httpx.AsyncClient:
    global _SNAPSHOT_HTTP_CLIENT
    if _SNAPSHOT_HTTP_CLIENT is None:
        _SNAPSHOT_HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(2.5, connect=1.0),
            limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
        )
    return _SNAPSHOT_HTTP_CLIENT


def _get_apriltag_detector(cv2, dictionary_name: str):
    detector = _APRILTAG_DETECTORS.get(dictionary_name)
    if detector is not None:
        return detector
    aruco = cv2.aruco
    dictionary = aruco.getPredefinedDictionary(getattr(aruco, dictionary_name))
    try:
        params = aruco.DetectorParameters()
        detector = ('new', aruco.ArucoDetector(dictionary, params), None)
    except Exception:
        params = aruco.DetectorParameters_create()
        detector = ('legacy', dictionary, params)
    _APRILTAG_DETECTORS[dictionary_name] = detector
    return detector


def _capture_frame_from_stream(url: str) -> bytes:
    return _STREAM_FRAME_CACHE.get_jpeg(url)


async def _download_snapshot(url: str, stream_url: str = '') -> bytes:
    candidates: list[str] = []
    snapshot = url.strip()
    stream = stream_url.strip()
    if snapshot:
        if _is_video_stream_url(snapshot):
            return await asyncio.to_thread(_capture_frame_from_stream, snapshot)
        candidates.append(snapshot)
    if stream_url:
        base = stream
        if _is_video_stream_url(base):
            return await asyncio.to_thread(_capture_frame_from_stream, base)
        if base.endswith('/stream'):
            candidates.extend([base[:-7] + '/snapshot', base[:-7] + '/snapshot.jpg'])
        elif base.endswith('/video_feed'):
            candidates.extend([base[:-11] + '/snapshot', base[:-11] + '/snapshot.jpg'])
        elif base.endswith('/video'):
            candidates.append(base[:-6] + '/shot.jpg')
    if not candidates:
        raise RuntimeError('Snapshot URL camera non configurato in Settings.')
    errors: list[str] = []
    client = _get_snapshot_http_client()
    for candidate in dict.fromkeys(candidates):
        try:
            sep = '&' if '?' in candidate else '?'
            response = await client.get(f'{candidate}{sep}t={int(time.time()*1000)}')
            if response.status_code < 400 and response.content:
                return response.content
            errors.append(f'{candidate} -> HTTP {response.status_code}')
        except Exception as exc:
            errors.append(f'{candidate} -> {exc}')
    raise RuntimeError('Snapshot camera non raggiungibile. Provati: ' + ' | '.join(errors))


def _detect_apriltags_from_jpeg(image_bytes: bytes, target_id: int | None = None, acquire_target: bool = False) -> dict[str, Any]:
    cv2, np = _load_cv2()
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError('Impossibile decodificare snapshot camera.')
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    if not hasattr(cv2, 'aruco'):
        raise RuntimeError('Modulo AprilTag non disponibile. Installa: pip install opencv-contrib-python-headless')

    aruco = cv2.aruco
    # Prova prima AprilTag 36h11, poi fallback ad altri dizionari AprilTag comuni.
    dict_names = ['DICT_APRILTAG_36h11', 'DICT_APRILTAG_25h9', 'DICT_APRILTAG_16h5']
    detections: list[dict[str, Any]] = []
    for name in dict_names:
        if not hasattr(aruco, name):
            continue
        detector_kind, detector, params = _get_apriltag_detector(cv2, name)
        if detector_kind == 'new':
            corners, ids, _ = detector.detectMarkers(gray)
        else:
            corners, ids, _ = aruco.detectMarkers(gray, detector, parameters=params)
        if ids is None or len(ids) == 0:
            continue
        for i, marker_corners in enumerate(corners):
            pts = marker_corners.reshape((4, 2)).astype(float)
            xs = pts[:, 0]
            ys = pts[:, 1]
            x = int(xs.min())
            y = int(ys.min())
            w = int(xs.max() - xs.min())
            h = int(ys.max() - ys.min())
            tag_id = int(ids[i][0])
            cx = float(xs.mean())
            cy = float(ys.mean())
            side = float(max(w, h))
            detections.append({
                'id': tag_id,
                'dictionary': name.replace('DICT_', ''),
                'x': x,
                'y': y,
                'w': w,
                'h': h,
                'cx': round(cx, 2),
                'cy': round(cy, 2),
                'side': round(side, 2),
                'area': int(max(1, w * h)),
                'corners': [[round(float(px), 2), round(float(py), 2)] for px, py in pts.tolist()],
            })
        if detections:
            break

    # Durante l'acquisizione scegli il tag più grande dentro il riquadro centrale.
    guide = {
        'x': int(width * 0.30),
        'y': int(height * 0.22),
        'w': int(width * 0.40),
        'h': int(height * 0.50),
    }

    def in_guide(tag: dict[str, Any]) -> bool:
        return guide['x'] <= tag['cx'] <= guide['x'] + guide['w'] and guide['y'] <= tag['cy'] <= guide['y'] + guide['h']

    candidates = detections
    if acquire_target:
        inside = [d for d in detections if in_guide(d)]
        if inside:
            candidates = inside
    elif target_id is not None:
        candidates = [d for d in detections if d['id'] == target_id]

    candidates.sort(key=lambda d: d['area'], reverse=True)
    tag = candidates[0] if candidates else None
    return {
        'width': width,
        'height': height,
        'guide': guide,
        'detections': detections,
        'tag': tag,
        'acquired_tag_id': tag['id'] if acquire_target and tag else None,
    }


def _compute_tag_follow_command(tag: dict[str, Any] | None, width: int, height: int, desired_size_ratio: float, max_linear: float, max_angular: float) -> dict[str, Any]:
    if not tag or not width or not height:
        return {'linear': 0.0, 'angular': 0.0, 'suggestion': 'AprilTag non rilevato: stop', 'offset_x': None, 'size_ratio': None}
    offset = (float(tag['cx']) - width / 2.0) / (width / 2.0)
    size_ratio = float(tag['side']) / float(min(width, height))
    distance_error = desired_size_ratio - size_ratio
    linear = 0.0
    if abs(distance_error) > 0.025:
        linear = max(-max_linear * 0.35, min(max_linear, distance_error * 0.90))
    angular = 0.0
    if abs(offset) > 0.05:
        angular = max(-max_angular, min(max_angular, -offset * max_angular))
    if abs(linear) < 0.012:
        linear = 0.0
    if abs(angular) < 0.012:
        angular = 0.0
    parts = []
    if linear > 0:
        parts.append('avanza')
    elif linear < 0:
        parts.append('arretra')
    if angular > 0:
        parts.append('ruota sinistra')
    elif angular < 0:
        parts.append('ruota destra')
    return {
        'linear': round(float(linear), 4),
        'angular': round(float(angular), 4),
        'suggestion': ' + '.join(parts) if parts else 'mantieni posizione',
        'offset_x': round(float(offset), 4),
        'size_ratio': round(float(size_ratio), 4),
    }


@app.post('/api/tracking/apriltag-step')
async def tracking_apriltag_step(payload: dict[str, Any] = Body(default_factory=dict)):
    settings = db.get_settings()
    snapshot_url = str(payload.get('snapshot_url') or settings.get('camera_snapshot_url') or '').strip()
    stream_url = str(payload.get('stream_url') or settings.get('camera_stream_url') or '').strip()
    follow_enabled = bool(payload.get('follow_enabled', False))
    acquire_target = bool(payload.get('acquire_target', False))
    target_id_raw = payload.get('target_id', None)
    target_id: int | None = None
    if target_id_raw not in (None, '', 'null'):
        try:
            target_id = int(target_id_raw)
        except Exception:
            target_id = None
    desired_size_ratio = float(payload.get('desired_size_ratio') or 0.18)
    max_linear = float(payload.get('max_linear') or 0.12)
    max_angular = float(payload.get('max_angular') or 0.35)
    result: dict[str, Any] = {'ok': False, 'follow_enabled': follow_enabled, 'target_id': target_id}
    try:
        image = await _download_snapshot(snapshot_url, stream_url)
        detection = _detect_apriltags_from_jpeg(image, target_id=target_id, acquire_target=acquire_target)
        command = _compute_tag_follow_command(detection['tag'], detection['width'], detection['height'], desired_size_ratio, max_linear, max_angular)
        result.update({'ok': True, **detection, 'command': command})
        if follow_enabled:
            action = _decide_follow_mission(command, desired_size_ratio)
            mission_result = await _run_follow_micro_mission(action, settings)
            result['mission_follow'] = mission_result
            result['drive_sent'] = bool(mission_result.get('sent'))
            result['drive_warning'] = '' if mission_result.get('sent') else str(mission_result.get('reason', 'micro-missione non inviata'))
        return result
    except Exception as exc:
        if follow_enabled:
            try:
                stop_res = await _run_follow_micro_mission('stop', settings)
                return {'ok': False, 'error': str(exc), 'mission_follow': stop_res, 'command': {'linear': 0.0, 'angular': 0.0, 'suggestion': 'errore: stop'}}
            except Exception:
                pass
        return {'ok': False, 'error': str(exc), 'command': {'linear': 0.0, 'angular': 0.0, 'suggestion': 'errore: stop'}}


# Compatibilità: se una vecchia pagina chiama ancora person-step, usa lo stesso motore AprilTag.
@app.post('/api/tracking/person-step')
async def tracking_person_step(payload: dict[str, Any] = Body(default_factory=dict)):
    return await tracking_apriltag_step(payload)


@app.post('/api/tracking/stop')
async def tracking_stop():
    settings = db.get_settings()
    try:
        result = await _run_follow_micro_mission('stop', settings)
        return {'ok': True, 'message': 'Stop tracking inviato', 'mission_follow': result}
    except Exception as exc:
        try:
            client = get_client()
            await client.clear_queue()
            return {'ok': True, 'message': f'Queue cancellata. Nota: {exc}'}
        except Exception as exc2:
            return {'ok': False, 'error': f'{exc}; clear_queue fallita: {exc2}'}


@app.get('/health')
def health():
    return {'ok': True}
