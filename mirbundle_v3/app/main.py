from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
from typing import Any
import asyncio
import json
import os
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
TRACKING_SETTINGS_PATH = BASE_DIR / 'data' / 'tracking_settings.json'
DEFAULT_TRACKING_SETTINGS: dict[str, Any] = {
    'targetSize': '32',
    'maxLinear': '1.50',
    'maxAngular': '1.50',
    'pidHz': '90',
    'detectWidth': '720',
}

_CV_CACHE: tuple[Any, Any] | None = None
_CV_LOCK = threading.Lock()


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


def _load_tracking_settings() -> dict[str, Any]:
    data = DEFAULT_TRACKING_SETTINGS.copy()
    try:
        if TRACKING_SETTINGS_PATH.exists():
            loaded = json.loads(TRACKING_SETTINGS_PATH.read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                data.update({k: str(v) for k, v in loaded.items() if k in data})
    except Exception:
        pass
    return data


def _save_tracking_settings(payload: dict[str, Any]) -> dict[str, Any]:
    data = DEFAULT_TRACKING_SETTINGS.copy()
    for key in data:
        value = payload.get(key, data[key])
        data[key] = str(value).strip() or data[key]
    TRACKING_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRACKING_SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return data


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
        tracking_settings=_load_tracking_settings(),
    )




@app.get('/joystick-lab', response_class=HTMLResponse)
def joystick_lab_page(request: Request):
    return render(request, 'joystick_lab.html')


@app.get('/api/tracking/settings')
def tracking_settings_get():
    return {'ok': True, 'settings': _load_tracking_settings()}


@app.post('/api/tracking/settings')
def tracking_settings_save(payload: dict[str, Any] = Body(default_factory=dict)):
    return {'ok': True, 'settings': _save_tracking_settings(payload)}


# ---------------- Tracking AprilTag + follow ----------------
def _load_cv2():
    global _CV_CACHE
    if _CV_CACHE is not None:
        return _CV_CACHE
    with _CV_LOCK:
        if _CV_CACHE is not None:
            return _CV_CACHE
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
            try:
                cv2.setUseOptimized(True)
                cv2.setNumThreads(max(2, min(12, os.cpu_count() or 8)))
            except Exception:
                pass
            _CV_CACHE = (cv2, np)
            return _CV_CACHE
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
        self.frame_ready = threading.Condition(self.lock)
        self.url = ''
        self.capture = None
        self.thread: threading.Thread | None = None
        self.running = False
        self.jpeg: bytes | None = None
        self.frame = None
        self.last_frame_ts = 0.0
        self.frame_id = 0
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

    def get_frame(self, url: str, wait_seconds: float = 2.0, min_frame_id: int = 0):
        url = (url or '').strip()
        if not url:
            raise RuntimeError('Stream URL camera non configurato.')
        self._ensure_running(url)
        deadline = time.time() + wait_seconds
        fallback = None
        fallback_age = 999.0
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            with self.frame_ready:
                if self.url == url and self.frame is not None and self.frame_id > min_frame_id:
                    return self.frame.copy()
                fallback = self.frame.copy() if self.url == url and self.frame is not None else None
                fallback_age = time.time() - self.last_frame_ts if self.last_frame_ts else 999.0
                last_error = self.last_error
                self.frame_ready.wait(timeout=min(0.012, remaining))
        if fallback is not None and fallback_age <= 0.35:
            return fallback
        raise RuntimeError(last_error or f'Nessun frame disponibile dallo stream: {url}')

    def _ensure_running(self, url: str) -> None:
        with self.lock:
            stale = bool(self.running and self.url == url and self.last_frame_ts and time.time() - self.last_frame_ts > 3.0)
            failed = bool(self.running and self.url == url and self.frame is None and self.last_error)
            if self.running and self.url == url and not stale and not failed:
                return
            self._stop_locked()
            self.url = url
            self.jpeg = None
            self.frame = None
            self.last_error = ''
            self.last_frame_ts = 0.0
            self.running = True
            self.thread = threading.Thread(target=self._loop, args=(url,), daemon=True)
            self.thread.start()

    def _stop_locked(self) -> None:
        self.running = False
        self.frame_ready.notify_all()
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
            self.url = ''
            self.jpeg = None
            self.frame = None
            self.last_frame_ts = 0.0
            self.frame_id = 0
            self.last_error = ''

    def _loop(self, url: str) -> None:
        cv2, _ = _load_cv2()
        cap = None
        try:
            while True:
                with self.lock:
                    if not self.running or self.url != url:
                        return
                if cap is None:
                    cap = cv2.VideoCapture(url)
                    with self.lock:
                        if self.url == url and self.running:
                            self.capture = cap
                    if hasattr(cv2, 'CAP_PROP_BUFFERSIZE'):
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    if hasattr(cv2, 'CAP_PROP_FPS'):
                        cap.set(cv2.CAP_PROP_FPS, 60)
                    if not cap.isOpened():
                        with self.frame_ready:
                            self.last_error = f'Stream video non raggiungibile: {url}'
                            self.frame_ready.notify_all()
                        try:
                            cap.release()
                        except Exception:
                            pass
                        cap = None
                        time.sleep(0.25)
                        continue
                ok, frame = cap.read()
                if not ok or frame is None:
                    with self.frame_ready:
                        self.last_error = f'Impossibile leggere un frame dallo stream: {url}'
                        self.frame_ready.notify_all()
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    time.sleep(0.12)
                    continue
                with self.lock:
                    if self.running and self.url == url:
                        self.frame = frame
                        self.jpeg = None
                        self.last_frame_ts = time.time()
                        self.frame_id += 1
                        self.last_error = ''
                        self.frame_ready.notify_all()
        finally:
            with self.lock:
                if self.capture is cap:
                    self.capture = None
                if self.url == url:
                    self.running = False
                    self.frame_ready.notify_all()
            if cap is not None:
                cap.release()


_STREAM_FRAME_CACHE = _StreamFrameCache()
_APRILTAG_DETECTORS: dict[str, Any] = {}


class _TrackerState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.target_id: int | None = None
        self.dictionary = ''
        self.last_tag: dict[str, Any] | None = None
        self.missed_frames = 0
        self.last_seen_ts = 0.0

    def snapshot(self, target_id: int | None, dictionary: str, previous_tag: dict[str, Any] | None, missed_frames: int) -> tuple[str, dict[str, Any] | None, int]:
        with self.lock:
            if target_id is not None and self.target_id == target_id:
                dictionary = dictionary or self.dictionary
                previous_tag = previous_tag or self.last_tag
                missed_frames = max(missed_frames, self.missed_frames)
            return dictionary, previous_tag, missed_frames

    def update(self, target_id: int | None, tag: dict[str, Any] | None) -> None:
        with self.lock:
            if tag:
                self.target_id = int(tag['id'])
                self.dictionary = str(tag.get('dictionary') or self.dictionary)
                self.last_tag = {
                    'x': tag.get('x'),
                    'y': tag.get('y'),
                    'w': tag.get('w'),
                    'h': tag.get('h'),
                    'cx': tag.get('cx'),
                    'cy': tag.get('cy'),
                    'side': tag.get('side'),
                }
                self.missed_frames = 0
                self.last_seen_ts = time.time()
            elif target_id is not None and self.target_id == target_id:
                self.missed_frames += 1
                if self.missed_frames > 8:
                    self.last_tag = None

    def reset(self) -> None:
        with self.lock:
            self.target_id = None
            self.dictionary = ''
            self.last_tag = None
            self.missed_frames = 0
            self.last_seen_ts = 0.0


_TRACKER_STATE = _TrackerState()


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
        if hasattr(params, 'cornerRefinementMethod') and hasattr(aruco, 'CORNER_REFINE_NONE'):
            params.cornerRefinementMethod = aruco.CORNER_REFINE_NONE
        if hasattr(params, 'adaptiveThreshWinSizeMin'):
            params.adaptiveThreshWinSizeMin = 3
        if hasattr(params, 'adaptiveThreshWinSizeMax'):
            params.adaptiveThreshWinSizeMax = 23
        if hasattr(params, 'adaptiveThreshWinSizeStep'):
            params.adaptiveThreshWinSizeStep = 10
        if hasattr(params, 'minMarkerPerimeterRate'):
            params.minMarkerPerimeterRate = 0.018
        if hasattr(params, 'maxErroneousBitsInBorderRate'):
            params.maxErroneousBitsInBorderRate = 0.35
        if hasattr(params, 'aprilTagQuadDecimate'):
            params.aprilTagQuadDecimate = 1.0
        if hasattr(params, 'aprilTagQuadSigma'):
            params.aprilTagQuadSigma = 0.0
        if hasattr(params, 'useAruco3Detection'):
            params.useAruco3Detection = True
        if hasattr(params, 'detectInvertedMarker'):
            params.detectInvertedMarker = False
        detector = ('new', aruco.ArucoDetector(dictionary, params), None)
    except Exception:
        params = aruco.DetectorParameters_create()
        detector = ('legacy', dictionary, params)
    _APRILTAG_DETECTORS[dictionary_name] = detector
    return detector


def _capture_frame_from_stream(url: str) -> bytes:
    frame = _STREAM_FRAME_CACHE.get_frame(url)
    cv2, _ = _load_cv2()
    encoded, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
    if not encoded:
        raise RuntimeError('Impossibile codificare frame camera.')
    return buffer.tobytes()


def _capture_raw_frame_from_stream(url: str, min_frame_id: int = 0):
    return _STREAM_FRAME_CACHE.get_frame(url, wait_seconds=0.08, min_frame_id=min_frame_id)


def _stream_frame_meta() -> dict[str, Any]:
    with _STREAM_FRAME_CACHE.lock:
        age_ms = (time.time() - _STREAM_FRAME_CACHE.last_frame_ts) * 1000 if _STREAM_FRAME_CACHE.last_frame_ts else None
        return {'frame_age_ms': round(age_ms, 1) if age_ms is not None else None, 'frame_id': _STREAM_FRAME_CACHE.frame_id}


async def _download_snapshot(url: str, stream_url: str = '') -> bytes:
    candidates: list[str] = []
    snapshot = url.strip()
    stream = stream_url.strip()
    if stream and _is_video_stream_url(stream):
        return await asyncio.to_thread(_capture_frame_from_stream, stream)
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


async def _load_tracking_frame(snapshot_url: str, stream_url: str = '', min_frame_id: int = 0):
    stream = stream_url.strip()
    snapshot = snapshot_url.strip()
    if stream and _is_video_stream_url(stream):
        return await asyncio.to_thread(_capture_raw_frame_from_stream, stream, min_frame_id)
    if snapshot and _is_video_stream_url(snapshot):
        return await asyncio.to_thread(_capture_raw_frame_from_stream, snapshot, min_frame_id)
    image = await _download_snapshot(snapshot, stream)
    cv2, np = _load_cv2()
    arr = np.frombuffer(image, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError('Impossibile decodificare snapshot camera.')
    return frame


def _detect_apriltags_from_frame(
    frame,
    target_id: int | None = None,
    target_dictionary: str = '',
    previous_tag: dict[str, Any] | None = None,
    acquire_target: bool = False,
    max_detect_width: int = 640,
    allow_dictionary_fallback: bool = True,
) -> dict[str, Any]:
    if frame is None:
        raise RuntimeError('Frame camera non disponibile.')
    cv2, _ = _load_cv2()
    height, width = frame.shape[:2]
    roi_x = 0
    roi_y = 0
    roi_w = width
    roi_h = height
    fast_roi = False
    if target_id is not None and not acquire_target and previous_tag:
        try:
            prev_cx = float(previous_tag.get('cx', 0))
            prev_cy = float(previous_tag.get('cy', 0))
            prev_side = max(float(previous_tag.get('side', 0)), float(previous_tag.get('w', 0)), float(previous_tag.get('h', 0)))
            if prev_cx > 0 and prev_cy > 0 and prev_side > 8:
                roi_side = max(260.0, min(float(max(width, height)), prev_side * 5.2))
                roi_x = max(0, int(round(prev_cx - roi_side / 2)))
                roi_y = max(0, int(round(prev_cy - roi_side / 2)))
                roi_w = min(width - roi_x, int(round(roi_side)))
                roi_h = min(height - roi_y, int(round(roi_side)))
                if roi_w >= 120 and roi_h >= 120 and roi_w * roi_h < width * height * 0.72:
                    fast_roi = True
        except Exception:
            fast_roi = False
    detect_frame = frame[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w] if fast_roi else frame
    scale = 1.0
    detect_base_width = detect_frame.shape[1]
    detect_base_height = detect_frame.shape[0]
    if max_detect_width > 0 and detect_base_width > max_detect_width:
        scale = detect_base_width / float(max_detect_width)
        detect_height = max(1, int(round(detect_base_height / scale)))
        detect_frame = cv2.resize(detect_frame, (max_detect_width, detect_height), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(detect_frame, cv2.COLOR_BGR2GRAY)

    if not hasattr(cv2, 'aruco'):
        raise RuntimeError('Modulo AprilTag non disponibile. Installa: pip install opencv-contrib-python-headless')

    aruco = cv2.aruco
    all_dict_names = ['DICT_APRILTAG_36h11', 'DICT_APRILTAG_25h9', 'DICT_APRILTAG_16h5']
    preferred = target_dictionary.strip()
    if preferred and not preferred.startswith('DICT_'):
        preferred = f'DICT_{preferred}'
    if preferred in all_dict_names:
        dict_names = [preferred] + [name for name in all_dict_names if name != preferred]
    elif target_id is not None and not acquire_target and not allow_dictionary_fallback:
        dict_names = ['DICT_APRILTAG_36h11']
    else:
        dict_names = all_dict_names
    if target_id is not None and not acquire_target and not allow_dictionary_fallback and preferred in all_dict_names:
        dict_names = [preferred]
    def run_detection(gray_image, scale_factor: float, offset_x: int, offset_y: int) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for name in dict_names:
            if not hasattr(aruco, name):
                continue
            detector_kind, detector, params = _get_apriltag_detector(cv2, name)
            if detector_kind == 'new':
                corners, ids, _ = detector.detectMarkers(gray_image)
            else:
                corners, ids, _ = aruco.detectMarkers(gray_image, detector, parameters=params)
            if ids is None or len(ids) == 0:
                continue
            for i, marker_corners in enumerate(corners):
                pts = marker_corners.reshape((4, 2)).astype(float) * scale_factor
                if offset_x or offset_y:
                    pts[:, 0] += offset_x
                    pts[:, 1] += offset_y
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
                found.append({
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
            if found:
                break
        return found

    detections = run_detection(gray, scale, roi_x if fast_roi else 0, roi_y if fast_roi else 0)
    if fast_roi and not detections:
        fast_roi = False
        detect_frame = frame
        scale = 1.0
        if max_detect_width > 0 and width > max_detect_width:
            scale = width / float(max_detect_width)
            detect_height = max(1, int(round(height / scale)))
            detect_frame = cv2.resize(detect_frame, (max_detect_width, detect_height), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(detect_frame, cv2.COLOR_BGR2GRAY)
        detections = run_detection(gray, scale, 0, 0)

    # Durante l'acquisizione scegli il tag più grande dentro il riquadro centrale.
    guide = {
        'x': 0,
        'y': 0,
        'w': int(width),
        'h': int(height),
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
        'acquired_tag_dictionary': tag['dictionary'] if acquire_target and tag else None,
        'fast_roi': fast_roi,
    }


def _compute_tag_tracking_command(tag: dict[str, Any] | None, width: int, height: int, desired_size_ratio: float, max_linear: float, max_angular: float) -> dict[str, Any]:
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
    acquire_target = bool(payload.get('acquire_target', False))
    target_id_raw = payload.get('target_id', None)
    target_dictionary = str(payload.get('target_dictionary') or '').strip()
    missed_frames = int(payload.get('missed_frames') or 0)
    previous_tag = payload.get('last_tag') if isinstance(payload.get('last_tag'), dict) else None
    target_id: int | None = None
    if target_id_raw not in (None, '', 'null'):
        try:
            target_id = int(target_id_raw)
        except Exception:
            target_id = None
    desired_size_ratio = float(payload.get('desired_size_ratio') or 0.18)
    max_linear = float(payload.get('max_linear') or 0.12)
    max_angular = float(payload.get('max_angular') or 0.35)
    max_detect_width_raw = payload.get('max_detect_width', 640)
    try:
        max_detect_width = int(max_detect_width_raw)
    except Exception:
        max_detect_width = 640
    last_frame_id = int(payload.get('last_frame_id') or 0)
    result: dict[str, Any] = {'ok': False, 'target_id': target_id}
    try:
        started = time.perf_counter()
        target_dictionary, previous_tag, missed_frames = _TRACKER_STATE.snapshot(target_id, target_dictionary, previous_tag, missed_frames)
        frame = await _load_tracking_frame(snapshot_url, stream_url, last_frame_id)
        loaded = time.perf_counter()
        detection = _detect_apriltags_from_frame(
            frame,
            target_id=target_id,
            target_dictionary=target_dictionary,
            previous_tag=previous_tag if missed_frames <= 1 else None,
            acquire_target=acquire_target,
            max_detect_width=max_detect_width,
            allow_dictionary_fallback=acquire_target or (target_id is None and missed_frames >= 2),
        )
        detected = time.perf_counter()
        _TRACKER_STATE.update(target_id, detection['tag'])
        command = _compute_tag_tracking_command(detection['tag'], detection['width'], detection['height'], desired_size_ratio, max_linear, max_angular)
        result.update({
            'ok': True,
            **detection,
            'command': command,
            'perf': {
                'load_ms': round((loaded - started) * 1000, 1),
                'detect_ms': round((detected - loaded) * 1000, 1),
                'total_ms': round((detected - started) * 1000, 1),
                **_stream_frame_meta(),
            },
        })
        return result
    except Exception as exc:
        return {
            'ok': False,
            'error': str(exc),
            'transient': True,
            'command': {'linear': 0.0, 'angular': 0.0, 'suggestion': 'errore camera: retry'},
            'perf': _stream_frame_meta(),
        }


@app.post('/api/tracking/stop')
async def tracking_stop():
    return {'ok': True, 'message': 'Stop tracking gestito via joystick ROSBridge dal browser.'}


@app.post('/api/tracking/reset')
async def tracking_reset():
    _STREAM_FRAME_CACHE.stop()
    _TRACKER_STATE.reset()
    return {'ok': True, 'message': 'Tracking e cache camera resettati'}


@app.get('/health')
def health():
    return {'ok': True}
