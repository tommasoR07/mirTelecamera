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
    'pidHz': '160',
    'detectWidth': '360',
    'linearKp': '1.85',
    'linearKi': '0.00',
    'linearKd': '0.20',
    'angularKp': '1.55',
    'angularKi': '0.00',
    'angularKd': '0.34',
    'contrastAlpha': '1.0',
    'brightnessBeta': '0.0',
    'claheClipLimit': '2.0',
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
    _APRILTAG_LIVE_DETECTOR.stop()
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


@app.post('/api/robot/ready')
async def robot_ready_api():
    try:
        client = get_client()
        await client.resume_ready()
        return {'ok': True, 'message': 'Comando Ready/Resume inviato'}
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}


@app.get('/api/robot/status')
async def robot_status_api():
    try:
        client = get_client()
        status = await client.get_status()
        if not isinstance(status, dict):
            status = {}
        state_text = str(status.get('state_text', '') or '')
        state_id = status.get('state_id', 0)
        is_paused = any(token in state_text.lower() for token in ('pause', 'paused', 'protective', 'error', 'emergency', 'abort')) or state_id in (4, 10)
        return {
            'ok': True,
            'state_text': state_text,
            'state_id': state_id,
            'is_paused': is_paused
        }
    except Exception as exc:
        import traceback
        traceback.print_exc()
        err_msg = str(exc) or type(exc).__name__
        return {'ok': False, 'error': err_msg}



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
async def tracking_page(request: Request):
    settings = db.get_settings()
    robot_status = None
    robot_warning = False
    try:
        client = get_client()
        robot_status = await client.get_status()
        state_text = str(getattr(robot_status, 'state_text', '') or '').lower()
        robot_warning = any(token in state_text for token in ('pause', 'paused', 'protective', 'error', 'emergency', 'abort'))
    except Exception:
        robot_status = None
    return render(
        request,
        'tracking.html',
        camera_stream_url=settings.get('camera_stream_url', ''),
        camera_snapshot_url=settings.get('camera_snapshot_url', ''),
        tracking_settings=_load_tracking_settings(),
        robot_status=robot_status,
        robot_warning=robot_warning,
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
                cv2.setNumThreads(max(2, min(16, os.cpu_count() or 8)))
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
                if self.url == url and self.frame is not None and self.last_frame_ts and time.time() - self.last_frame_ts <= 0.35:
                    frame = self.frame
                    break
                last_error = self.last_error
            time.sleep(0.03)
        else:
            raise RuntimeError(last_error or f'Nessun frame disponibile dallo stream: {url}')
        cv2, _ = _load_cv2()
        encoded, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 68])
        if not encoded:
            raise RuntimeError('Impossibile codificare frame camera.')
        jpeg = buffer.tobytes()
        with self.lock:
            if self.url == url:
                self.jpeg = jpeg
        return jpeg

    def get_frame(self, url: str, wait_seconds: float = 2.0, min_frame_id: int = 0):
        url = (url or '').strip()
        if not url:
            raise RuntimeError('Stream URL camera non configurato.')
        self._ensure_running(url)
        deadline = time.time() + wait_seconds
        fallback = None
        fallback_age = 999.0
        first_wait = True
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            with self.frame_ready:
                if self.url == url and self.frame is not None:
                    fallback = self.frame
                    fallback_age = time.time() - self.last_frame_ts if self.last_frame_ts else 999.0
                    if self.frame_id > min_frame_id or fallback_age <= 0.095:
                        return self.frame
                last_error = self.last_error
                self.frame_ready.wait(timeout=min(0.006 if first_wait else 0.010, remaining))
            first_wait = False
        if fallback is not None and fallback_age <= 0.14:
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
                    if url.lower().startswith('rtsp://'):
                        os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS', 'rtsp_transport;udp|fflags;nobuffer|flags;low_delay|max_delay;0')
                    cap = cv2.VideoCapture(url)
                    with self.lock:
                        if self.url == url and self.running:
                            self.capture = cap
                    if hasattr(cv2, 'CAP_PROP_BUFFERSIZE'):
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    if hasattr(cv2, 'CAP_PROP_FPS'):
                        cap.set(cv2.CAP_PROP_FPS, 120)
                    if hasattr(cv2, 'CAP_PROP_OPEN_TIMEOUT_MSEC'):
                        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 900)
                    if hasattr(cv2, 'CAP_PROP_READ_TIMEOUT_MSEC'):
                        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 350)
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
            timeout=httpx.Timeout(0.9, connect=0.35),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
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
            params.adaptiveThreshWinSizeMax = 7
        if hasattr(params, 'adaptiveThreshWinSizeStep'):
            params.adaptiveThreshWinSizeStep = 6
        if hasattr(params, 'minMarkerPerimeterRate'):
            params.minMarkerPerimeterRate = 0.008
        if hasattr(params, 'maxErroneousBitsInBorderRate'):
            params.maxErroneousBitsInBorderRate = 0.35
        if hasattr(params, 'aprilTagQuadDecimate'):
            params.aprilTagQuadDecimate = 1.8
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
    return _STREAM_FRAME_CACHE.get_frame(url, wait_seconds=0.080, min_frame_id=min_frame_id)


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
    far_search: bool = False,
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
    if target_id is not None and not acquire_target and previous_tag and not far_search:
        try:
            prev_cx = float(previous_tag.get('cx', 0))
            prev_cy = float(previous_tag.get('cy', 0))
            prev_vx = float(previous_tag.get('vx', 0) or 0)
            prev_vy = float(previous_tag.get('vy', 0) or 0)
            prev_side = max(float(previous_tag.get('side', 0)), float(previous_tag.get('w', 0)), float(previous_tag.get('h', 0)))
            if prev_cx > 0 and prev_cy > 0 and prev_side > 8:
                lead_seconds = 0.050
                predicted_cx = max(0.0, min(float(width), prev_cx + prev_vx * lead_seconds))
                predicted_cy = max(0.0, min(float(height), prev_cy + prev_vy * lead_seconds))
                speed_pad = min(120.0, (abs(prev_vx) + abs(prev_vy)) * 0.030)
                roi_side = max(145.0 + speed_pad, min(float(max(width, height)), prev_side * 2.55 + speed_pad))
                roi_x = max(0, int(round(predicted_cx - roi_side / 2)))
                roi_y = max(0, int(round(predicted_cy - roi_side / 2)))
                roi_w = min(width - roi_x, int(round(roi_side)))
                roi_h = min(height - roi_y, int(round(roi_side)))
                if roi_w >= 110 and roi_h >= 110 and roi_w * roi_h < width * height * 0.90:
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
    settings = _load_tracking_settings()
    contrast_alpha = float(settings.get('contrastAlpha', 1.0))
    brightness_beta = float(settings.get('brightnessBeta', 0.0))
    clahe_clip_limit = float(settings.get('claheClipLimit', 2.0))
    if abs(contrast_alpha - 1.0) > 0.01 or abs(brightness_beta) > 0.01:
        gray = cv2.convertScaleAbs(gray, alpha=contrast_alpha, beta=brightness_beta)
    if clahe_clip_limit > 0.01:
        clahe = cv2.createCLAHE(clipLimit=clahe_clip_limit, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

    if not hasattr(cv2, 'aruco'):
        raise RuntimeError('Modulo AprilTag non disponibile. Installa: pip install opencv-contrib-python-headless')

    aruco = cv2.aruco
    all_dict_names = ['DICT_APRILTAG_25h9', 'DICT_APRILTAG_36h11', 'DICT_APRILTAG_16h5']
    preferred = target_dictionary.strip()
    if preferred and not preferred.startswith('DICT_'):
        preferred = f'DICT_{preferred}'
    if preferred in all_dict_names:
        dict_names = [preferred]
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
        center_x = roi_x + roi_w / 2.0
        center_y = roi_y + roi_h / 2.0
        expanded_side = min(float(max(width, height)), max(float(roi_w), float(roi_h)) * 1.75)
        expanded_x = max(0, int(round(center_x - expanded_side / 2)))
        expanded_y = max(0, int(round(center_y - expanded_side / 2)))
        expanded_w = min(width - expanded_x, int(round(expanded_side)))
        expanded_h = min(height - expanded_y, int(round(expanded_side)))
        detect_frame = frame[expanded_y:expanded_y + expanded_h, expanded_x:expanded_x + expanded_w]
        scale = 1.0
        retry_width = max(max_detect_width, 520)
        if retry_width > 0 and detect_frame.shape[1] > retry_width:
            scale = detect_frame.shape[1] / float(retry_width)
            detect_height = max(1, int(round(detect_frame.shape[0] / scale)))
            detect_frame = cv2.resize(detect_frame, (retry_width, detect_height), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(detect_frame, cv2.COLOR_BGR2GRAY)
        detections = run_detection(gray, scale, expanded_x, expanded_y)
        if not detections:
            fast_roi = False
    elif not detections and far_search and max_detect_width < 900 and width > max_detect_width:
        retry_width = min(width, 900)
        scale = width / float(retry_width)
        detect_height = max(1, int(round(height / scale)))
        detect_frame = cv2.resize(frame, (retry_width, detect_height), interpolation=cv2.INTER_AREA)
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
        'far_search': far_search,
    }


class _AprilTagLiveDetector:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ready = threading.Condition(self.lock)
        self.url = ''
        self.config: dict[str, Any] = {}
        self.result: dict[str, Any] | None = None
        self.result_ts = 0.0
        self.result_frame_id = 0
        self.thread: threading.Thread | None = None
        self.running = False
        self.last_error = ''

    def configure(self, url: str, config: dict[str, Any]) -> None:
        url = (url or '').strip()
        if not url:
            return
        with self.lock:
            same_url = self.running and self.url == url
            self.config = config.copy()
            if same_url:
                return
            self.running = False
            self.ready.notify_all()
            self.url = url
            self.result = None
            self.result_ts = 0.0
            self.result_frame_id = 0
            self.last_error = ''
            self.running = True
            self.thread = threading.Thread(target=self._loop, args=(url,), daemon=True)
            self.thread.start()

    def latest(self, min_frame_id: int = 0, max_age: float = 0.15, wait_seconds: float = 0.040) -> dict[str, Any] | None:
        deadline = time.time() + wait_seconds
        with self.ready:
            while True:
                if self.result is not None and self.result_frame_id > min_frame_id and time.time() - self.result_ts <= max_age:
                    return self.result.copy()
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self.ready.wait(timeout=min(0.006, remaining))

    def stop(self) -> None:
        with self.ready:
            self.running = False
            self.url = ''
            self.result = None
            self.result_ts = 0.0
            self.result_frame_id = 0
            self.last_error = ''
            self.ready.notify_all()

    def _loop(self, url: str) -> None:
        last_frame_id = 0
        previous_tag: dict[str, Any] | None = None
        live_misses = 0
        while True:
            with self.ready:
                if not self.running or self.url != url:
                    return
                config = self.config.copy()
            try:
                frame = _STREAM_FRAME_CACHE.get_frame(url, wait_seconds=0.100, min_frame_id=last_frame_id)
                with _STREAM_FRAME_CACHE.lock:
                    current_frame_id = _STREAM_FRAME_CACHE.frame_id
                if current_frame_id == last_frame_id:
                    time.sleep(0.002)
                    continue
                last_frame_id = current_frame_id
                started = time.perf_counter()
                detection = _detect_apriltags_from_frame(
                    frame,
                    target_id=config.get('target_id'),
                    target_dictionary=str(config.get('target_dictionary') or 'APRILTAG_25h9'),
                    previous_tag=previous_tag if live_misses == 0 else None,
                    acquire_target=False,
                    max_detect_width=int(config.get('max_detect_width') or 360),
                    allow_dictionary_fallback=False,
                    far_search=bool(config.get('far_search', False) and live_misses >= 2),
                )
                detected = time.perf_counter()
                if detection.get('tag'):
                    previous_tag = detection.get('tag')
                    live_misses = 0
                else:
                    live_misses += 1
                detection['perf'] = {
                    'load_ms': 0.0,
                    'detect_ms': round((detected - started) * 1000, 1),
                    'total_ms': round((detected - started) * 1000, 1),
                    **_stream_frame_meta(),
                }
                with self.ready:
                    if self.running and self.url == url:
                        self.result = detection
                        self.result_ts = time.time()
                        self.result_frame_id = current_frame_id
                        self.last_error = ''
                        self.ready.notify_all()
            except Exception as exc:
                with self.ready:
                    if self.running and self.url == url:
                        self.last_error = str(exc)
                        self.ready.notify_all()
                time.sleep(0.010)


_APRILTAG_LIVE_DETECTOR = _AprilTagLiveDetector()


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


_BACKEND_TRACKING_ACTIVE = False
_BACKEND_TRACKING_TASK: asyncio.Task | None = None
_ROS_BRIDGE_CLIENT: _ROSBridgeClient | None = None
_LAST_TRACKING_STATUS: dict[str, Any] = {'tracking_active': False}
_PID_CONTROLLER = _PIDController()


class _ROSBridgeClient:
    def __init__(self, host: str) -> None:
        self.host = host
        self.joystick_token = None
        self.ws = None
        self.running = False
        self.thread = None
        self.loop = None
        self.lock = threading.Lock()

    async def _connect_and_loop(self):
        import websockets
        from urllib.parse import urlparse
        
        clean_host = self.host.strip()
        if not clean_host.startswith(('http://', 'https://', 'ws://', 'wss://')):
            clean_host = f"http://{clean_host}"
        try:
            parsed = urlparse(clean_host)
            hostname = parsed.hostname or self.host
        except Exception:
            hostname = self.host
            
        ws_url = f"ws://{hostname}:9090"
        
        while self.running:
            try:
                async with websockets.connect(ws_url, open_timeout=3.0) as ws:
                    with self.lock:
                        self.ws = ws
                    await ws.send(json.dumps({
                        "op": "advertise",
                        "topic": "/joystick_vel",
                        "type": "mirMsgs/JoystickVel"
                    }))
                    await ws.send(json.dumps({
                        "op": "call_service",
                        "service": "/mirsupervisor/setRobotState",
                        "type": "mirSupervisor/SetState",
                        "args": {
                            "robotState": 11,
                            "web_session_id": "MIRITISCUNEO"
                        },
                        "id": f"tracking_backend_{int(time.time())}"
                    }))
                    async for message in ws:
                        if not self.running:
                            break
                        try:
                            data = json.loads(message)
                            token = data.get("values", {}).get("joystick_token") or data.get("result", {}).get("joystick_token")
                            if token:
                                with self.lock:
                                    self.joystick_token = token
                        except Exception:
                            pass
            except Exception:
                with self.lock:
                    self.ws = None
                    self.joystick_token = None
                await asyncio.sleep(1.0)

    def start(self) -> None:
        with self.lock:
            if self.running:
                return
            self.running = True
            self.joystick_token = None
            self.ws = None
            
        def thread_target():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._connect_and_loop())
            self.loop.close()
            
        self.thread = threading.Thread(target=thread_target, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        with self.lock:
            if not self.running:
                return
            self.running = False
            
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
            
        if self.thread:
            try:
                self.thread.join(timeout=1.0)
            except Exception:
                pass
            self.thread = None
        self.ws = None
        self.joystick_token = None

    async def _send_velocity_cmd(self, linear: float, angular: float):
        ws = self.ws
        token = self.joystick_token
        if ws and token:
            msg = {
                "op": "publish",
                "topic": "/joystick_vel",
                "msg": {
                    "joystick_token": token,
                    "speed_command": {
                        "linear": { "x": linear, "y": 0.0, "z": 0.0 },
                        "angular": { "x": 0.0, "y": 0.0, "z": angular }
                    }
                }
            }
            await ws.send(json.dumps(msg))

    def publish_velocity(self, linear: float, angular: float) -> bool:
        with self.lock:
            if not self.running or not self.ws or not self.joystick_token:
                return False
            loop = self.loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._send_velocity_cmd(linear, angular), loop)
            return True
        return False


class _PIDController:
    def __init__(self) -> None:
        self.last_ts = 0.0
        self.filtered_offset = None
        self.filtered_size = None
        self.offset_velocity = 0.0
        self.size_velocity = 0.0
        self.last_offset_sign = 0
        self.last_oscillation_at = 0.0
        self.oscillation_score = 0.0
        self.chill_stop_until = 0.0
        self.chill_forward_until = 0.0
        self.stable_frames = 0
        self.curve_in_place = False
        self.last_linear = 0.0
        self.last_angular = 0.0
        self.missed_frames = 0
        self.visible_frames = 0
        self.control_mode = "idle"
        self.last_tag = None

    def reset(self) -> None:
        self.last_ts = 0.0
        self.filtered_offset = None
        self.filtered_size = None
        self.offset_velocity = 0.0
        self.size_velocity = 0.0
        self.last_offset_sign = 0
        self.last_oscillation_at = 0.0
        self.oscillation_score = 0.0
        self.chill_stop_until = 0.0
        self.chill_forward_until = 0.0
        self.stable_frames = 0
        self.curve_in_place = False
        self.last_linear = 0.0
        self.last_angular = 0.0
        self.missed_frames = 0
        self.visible_frames = 0
        self.control_mode = "idle"
        self.last_tag = None

    def compute(self, size_ratio: float | None, offset_x: float | None, settings: dict[str, Any]) -> tuple[float, float, str]:
        now = time.perf_counter()
        now_ms = now * 1000.0
        
        dt = min(0.10, max(0.008, now - self.last_ts)) if self.last_ts > 0.0 else 0.016
        self.last_ts = now
        
        if size_ratio is None or offset_x is None:
            return 0.0, 0.0, "idle"
            
        desired = float(settings.get('targetSize', 32)) / 100.0
        max_linear = min(float(settings.get('maxLinear', 1.5)), 1.65)
        max_angular = min(float(settings.get('maxAngular', 1.65)), 1.85)
        
        linear_kp = float(settings.get('linearKp', 1.85))
        linear_kd = float(settings.get('linearKd', 0.20))
        angular_kp = float(settings.get('angularKp', 1.55))
        angular_kd = float(settings.get('angularKd', 0.34))
        
        offset_alpha = min(0.78, max(0.38, dt / (0.010 + dt)))
        size_alpha = min(0.62, max(0.24, dt / (0.022 + dt)))
        
        previous_offset = self.filtered_offset if self.filtered_offset is not None else offset_x
        previous_size = self.filtered_size if self.filtered_size is not None else size_ratio
        
        self.filtered_offset = previous_offset + (offset_x - previous_offset) * offset_alpha
        self.filtered_size = previous_size + (size_ratio - previous_size) * size_alpha
        
        raw_offset_velocity = (self.filtered_offset - previous_offset) / dt
        raw_size_velocity = (self.filtered_size - previous_size) / dt
        
        velocity_alpha = min(0.58, max(0.18, dt / (0.030 + dt)))
        self.offset_velocity += (raw_offset_velocity - self.offset_velocity) * velocity_alpha
        self.size_velocity += (raw_size_velocity - self.size_velocity) * velocity_alpha
        
        prediction_lead = min(0.028, max(0.010, 0.010 + dt * 0.45))
        predicted_offset = self.filtered_offset + self.offset_velocity * prediction_lead
        offset_error = min(1.0, max(-1.0, predicted_offset))
        distance_error = desired - self.filtered_size
        
        offset_deadband = 0.040
        distance_deadband = 0.014
        abs_offset = abs(offset_error)
        abs_distance = abs(distance_error)
        
        offset_sign = 1 if offset_error > offset_deadband else (-1 if offset_error < -offset_deadband else 0)
        sign_flip = offset_sign != 0 and self.last_offset_sign != 0 and offset_sign != self.last_offset_sign
        fast_flip = sign_flip and (now_ms - self.last_oscillation_at < 760.0)
        
        if sign_flip and abs(self.last_angular) > max_angular * 0.08:
            self.oscillation_score = min(6.0, self.oscillation_score + (1.3 if fast_flip else 0.8))
            if self.oscillation_score >= 1.6:
                self.chill_stop_until = now_ms + 420.0
                self.chill_forward_until = now_ms + 1550.0
        elif not fast_flip and (now_ms - self.last_oscillation_at > 900.0):
            self.oscillation_score = max(0.0, self.oscillation_score - 0.45)
            
        if sign_flip:
            self.last_oscillation_at = now_ms
        if offset_sign != 0:
            self.last_offset_sign = offset_sign
            
        lateral_velocity = self.offset_velocity
        distance_velocity = -self.size_velocity
        closing_too_fast = max(0.0, -distance_velocity) if distance_error > 0 else max(0.0, distance_velocity)
        
        stable_now = abs_offset < 0.075 and abs(lateral_velocity) < 0.28
        self.stable_frames = min(40, self.stable_frames + 1) if stable_now else 0
        
        curve_enter = 0.30
        curve_exit = 0.16
        self.curve_in_place = abs_offset > curve_enter or (self.curve_in_place and abs_offset > curve_exit)
        
        angular_target = 0.0
        if abs_offset > offset_deadband:
            normalized = angular_kp * offset_error + angular_kd * lateral_velocity
            t = min(1.0, max(0.0, (abs_offset - offset_deadband) / (0.42 - offset_deadband)))
            authority = t * t * (3.0 - 2.0 * t)
            
            if self.curve_in_place:
                limit = max_angular * min(0.67, max(0.0, 0.22 + authority * 0.45))
            else:
                limit = max_angular * min(0.74, max(0.0, 0.16 + authority * 0.58))
            angular_target = -min(1.0, max(-1.0, normalized)) * limit
            if (angular_target > 0) == (self.last_angular > 0) and abs(angular_target) < abs(self.last_angular) * 0.35:
                angular_target = self.last_angular * 0.35
                
        linear_target = 0.0
        if now_ms >= self.chill_stop_until:
            t_align = min(1.0, max(0.0, (abs_offset - 0.16) / 0.30))
            alignment_gate = 1.0 - (t_align * t_align * (3.0 - 2.0 * t_align))
            
            t_ang = min(1.0, max(0.0, (abs(self.last_angular) - max_angular * 0.28) / (max_angular * 0.44)))
            angular_gate = 1.0 - (t_ang * t_ang * (3.0 - 2.0 * t_ang))
            
            gate = min(1.0, max(0.0, alignment_gate * angular_gate))
            
            if distance_error > distance_deadband:
                normalized = linear_kp * distance_error - linear_kd * closing_too_fast
                linear_target = max_linear * min(1.0, max(0.0, normalized)) * gate
            elif distance_error < -distance_deadband * 1.7 and abs_offset < 0.14:
                t_rev = min(1.0, max(0.0, (-distance_error - distance_deadband * 1.5) / (0.14 - distance_deadband * 1.5)))
                reverse_profile = t_rev * t_rev * (3.0 - 2.0 * t_rev)
                linear_target = -max_linear * 0.12 * reverse_profile
                
        if abs_offset < 0.035 and abs(lateral_velocity) < 0.22:
            angular_target = 0.0
        if abs_distance < 0.012 and abs(self.size_velocity) < 0.09:
            linear_target = 0.0
            
        if now_ms < self.chill_stop_until:
            self.last_angular = 0.0
            self.last_linear = 0.0
            self.control_mode = "anti-ondulazione stop"
            return 0.0, 0.0, self.control_mode
            
        if now_ms < self.chill_forward_until:
            t_settle = min(1.0, max(0.0, (now_ms - self.chill_stop_until) / 1130.0))
            settle = t_settle * t_settle * (3.0 - 2.0 * t_settle)
            angular_target *= 0.22
            linear_target = max(linear_target, max_linear * (0.035 + settle * 0.055))
            linear_target = min(linear_target, max_linear * 0.10)
            
        if (angular_target > 0) != (self.last_angular > 0):
            angular_step = max_angular * dt * 7.5
        elif abs(angular_target) < abs(self.last_angular):
            angular_step = max_angular * dt * 10.0
        else:
            angular_step = max_angular * dt * (6.2 if self.curve_in_place else 7.4)
            
        linear_accel = max_linear * dt * 3.4
        linear_brake = max_linear * dt * 6.2
        linear_step = linear_brake if abs(linear_target) < abs(self.last_linear) else linear_accel
        
        angular = min(self.last_angular + angular_step, max(self.last_angular - angular_step, angular_target))
        linear = min(self.last_linear + linear_step, max(self.last_linear - linear_step, linear_target))
        
        self.last_angular = angular
        self.last_linear = linear
        
        if now_ms < self.chill_forward_until:
            self.control_mode = "anti-ondulazione avanti chill"
        elif self.curve_in_place:
            self.control_mode = "curva smorzata"
        elif stable_now:
            self.control_mode = "stabile"
        else:
            if abs_offset > 0.18:
                self.control_mode = "riallineamento"
            elif abs(angular) > 0.18:
                self.control_mode = "micro-correzione"
            else:
                self.control_mode = "tracking"
                
        return linear, angular, self.control_mode


async def _backend_tracking_loop():
    global _BACKEND_TRACKING_ACTIVE, _ROS_BRIDGE_CLIENT, _PID_CONTROLLER, _LAST_TRACKING_STATUS
    settings = _load_tracking_settings()
    host = settings.get('host') or db.get_settings().get('host', '')
    if not host:
        _BACKEND_TRACKING_ACTIVE = False
        return
        
    _ROS_BRIDGE_CLIENT = _ROSBridgeClient(host)
    _ROS_BRIDGE_CLIENT.start()
    _PID_CONTROLLER.reset()
    
    # Wait for connection and joystick token
    for _ in range(30):
        if not _BACKEND_TRACKING_ACTIVE:
            break
        if _ROS_BRIDGE_CLIENT.joystick_token:
            break
        await asyncio.sleep(0.1)
        
    last_frame_id = 0
    while _BACKEND_TRACKING_ACTIVE:
        started = time.perf_counter()
        settings = _load_tracking_settings()
        
        hz = max(10.0, min(180.0, float(settings.get('pidHz', 160))))
        target_dt = 1.0 / hz
        
        url = settings.get('camera_stream_url', '').strip()
        if not url:
            await asyncio.sleep(0.05)
            continue
            
        try:
            frame = await asyncio.to_thread(_capture_raw_frame_from_stream, url, last_frame_id)
            with _STREAM_FRAME_CACHE.lock:
                current_frame_id = _STREAM_FRAME_CACHE.frame_id
            last_frame_id = current_frame_id
            
            target_id = None
            target_id_raw = settings.get('target_id')
            if target_id_raw not in (None, '', 'null'):
                try:
                    target_id = int(target_id_raw)
                except Exception:
                    pass
                    
            detection = _detect_apriltags_from_frame(
                frame,
                target_id=target_id,
                target_dictionary=str(settings.get('target_dictionary') or 'APRILTAG_25h9'),
                previous_tag=_PID_CONTROLLER.last_tag if _PID_CONTROLLER.missed_frames == 0 else None,
                acquire_target=False,
                max_detect_width=int(settings.get('detectWidth', 360)),
                allow_dictionary_fallback=False,
                far_search=bool(settings.get('far_search') and _PID_CONTROLLER.missed_frames >= 2)
            )
            
            tag = detection.get('tag')
            if tag:
                _PID_CONTROLLER.visible_frames += 1
                _PID_CONTROLLER.missed_frames = 0
                
                desired_size_ratio = float(settings.get('targetSize', 32)) / 100.0
                max_linear = float(settings.get('maxLinear', 1.5))
                max_angular = float(settings.get('maxAngular', 1.65))
                
                command = _compute_tag_tracking_command(tag, detection['width'], detection['height'], desired_size_ratio, max_linear, max_angular)
                linear, angular, mode = _PID_CONTROLLER.compute(command['size_ratio'], command['offset_x'], settings)
                
                _ROS_BRIDGE_CLIENT.publish_velocity(linear, angular)
                
                _LAST_TRACKING_STATUS = {
                    'ok': True,
                    'tag': tag,
                    'command': {
                        'linear': linear,
                        'angular': angular,
                        'suggestion': command['suggestion'],
                        'offset_x': command['offset_x'],
                        'size_ratio': command['size_ratio'],
                        'mode': mode
                    },
                    'perf': {
                        'detect_ms': round((time.perf_counter() - started) * 1000, 1),
                        'total_ms': round((time.perf_counter() - started) * 1000, 1),
                        'frame_id': current_frame_id,
                        'frame_age_ms': round((time.time() - _STREAM_FRAME_CACHE.last_frame_ts) * 1000, 1) if _STREAM_FRAME_CACHE.last_frame_ts else 0.0
                    },
                    'missed_frames': 0,
                    'tracking_active': True,
                    'rosbridge_connected': bool(_ROS_BRIDGE_CLIENT.ws and _ROS_BRIDGE_CLIENT.joystick_token)
                }
                
                _PID_CONTROLLER.last_tag = {
                    'x': tag.get('x'),
                    'y': tag.get('y'),
                    'w': tag.get('w'),
                    'h': tag.get('h'),
                    'cx': tag.get('cx'),
                    'cy': tag.get('cy'),
                    'side': tag.get('side'),
                    'vx': 0.0,
                    'vy': 0.0
                }
            else:
                _PID_CONTROLLER.missed_frames += 1
                _PID_CONTROLLER.visible_frames = 0
                
                if _PID_CONTROLLER.missed_frames <= 5:
                    _PID_CONTROLLER.last_linear *= 0.85
                    _PID_CONTROLLER.last_angular *= 0.85
                    if abs(_PID_CONTROLLER.last_linear) < 0.012:
                        _PID_CONTROLLER.last_linear = 0.0
                    if abs(_PID_CONTROLLER.last_angular) < 0.012:
                        _PID_CONTROLLER.last_angular = 0.0
                    _ROS_BRIDGE_CLIENT.publish_velocity(_PID_CONTROLLER.last_linear, _PID_CONTROLLER.last_angular)
                    mode = "ricerca coast"
                else:
                    _ROS_BRIDGE_CLIENT.publish_velocity(0.0, 0.0)
                    _PID_CONTROLLER.reset()
                    mode = "target perso"
                    
                _LAST_TRACKING_STATUS = {
                    'ok': True,
                    'tag': None,
                    'command': {
                        'linear': _PID_CONTROLLER.last_linear,
                        'angular': _PID_CONTROLLER.last_angular,
                        'suggestion': 'AprilTag non visibile',
                        'offset_x': None,
                        'size_ratio': None,
                        'mode': mode
                    },
                    'perf': {
                        'detect_ms': round((time.perf_counter() - started) * 1000, 1),
                        'total_ms': round((time.perf_counter() - started) * 1000, 1),
                        'frame_id': current_frame_id,
                        'frame_age_ms': round((time.time() - _STREAM_FRAME_CACHE.last_frame_ts) * 1000, 1) if _STREAM_FRAME_CACHE.last_frame_ts else 0.0
                    },
                    'missed_frames': _PID_CONTROLLER.missed_frames,
                    'tracking_active': True,
                    'rosbridge_connected': bool(_ROS_BRIDGE_CLIENT.ws and _ROS_BRIDGE_CLIENT.joystick_token)
                }
                
        except Exception as exc:
            _PID_CONTROLLER.missed_frames += 1
            _PID_CONTROLLER.visible_frames = 0
            
            if _PID_CONTROLLER.missed_frames <= 5:
                _PID_CONTROLLER.last_linear *= 0.85
                _PID_CONTROLLER.last_angular *= 0.85
                if abs(_PID_CONTROLLER.last_linear) < 0.012:
                    _PID_CONTROLLER.last_linear = 0.0
                if abs(_PID_CONTROLLER.last_angular) < 0.012:
                    _PID_CONTROLLER.last_angular = 0.0
                if _ROS_BRIDGE_CLIENT:
                    _ROS_BRIDGE_CLIENT.publish_velocity(_PID_CONTROLLER.last_linear, _PID_CONTROLLER.last_angular)
                mode = "camera retry coast"
            else:
                if _ROS_BRIDGE_CLIENT:
                    _ROS_BRIDGE_CLIENT.publish_velocity(0.0, 0.0)
                _PID_CONTROLLER.reset()
                mode = "camera error"
                
            _LAST_TRACKING_STATUS = {
                'ok': False,
                'error': str(exc),
                'command': {
                    'linear': _PID_CONTROLLER.last_linear,
                    'angular': _PID_CONTROLLER.last_angular,
                    'suggestion': 'errore camera',
                    'offset_x': None,
                    'size_ratio': None,
                    'mode': mode
                },
                'perf': {
                    'detect_ms': 0.0,
                    'total_ms': 0.0,
                    'frame_id': 0,
                    'frame_age_ms': 0.0
                },
                'missed_frames': _PID_CONTROLLER.missed_frames,
                'tracking_active': True,
                'rosbridge_connected': bool(_ROS_BRIDGE_CLIENT and _ROS_BRIDGE_CLIENT.ws and _ROS_BRIDGE_CLIENT.joystick_token)
            }
            
        elapsed = time.perf_counter() - started
        sleep_time = max(0.001, target_dt - elapsed)
        await asyncio.sleep(sleep_time)
        
    if _ROS_BRIDGE_CLIENT:
        _ROS_BRIDGE_CLIENT.publish_velocity(0.0, 0.0)
        _ROS_BRIDGE_CLIENT.stop()
        _ROS_BRIDGE_CLIENT = None
    _PID_CONTROLLER.reset()
    _LAST_TRACKING_STATUS = {'tracking_active': False}


@app.post('/api/tracking/start')
async def tracking_start(payload: dict[str, Any] = Body(default_factory=dict)):
    global _BACKEND_TRACKING_ACTIVE, _BACKEND_TRACKING_TASK
    
    settings = _load_tracking_settings()
    settings.update({
        'target_id': str(payload.get('target_id') or settings.get('target_id', '')).strip(),
        'target_dictionary': str(payload.get('target_dictionary') or settings.get('target_dictionary', '')).strip(),
    })
    _save_tracking_settings(settings)
    
    if _BACKEND_TRACKING_ACTIVE:
        return {'ok': True, 'message': 'Tracking già attivo sul backend.'}
        
    _BACKEND_TRACKING_ACTIVE = True
    _BACKEND_TRACKING_TASK = asyncio.create_task(_backend_tracking_loop())
    return {'ok': True, 'message': 'Inseguimento avviato sul backend.'}


@app.post('/api/tracking/stop')
async def tracking_stop():
    global _BACKEND_TRACKING_ACTIVE, _BACKEND_TRACKING_TASK
    if not _BACKEND_TRACKING_ACTIVE:
        return {'ok': True, 'message': 'Tracking non attivo.'}
        
    _BACKEND_TRACKING_ACTIVE = False
    if _BACKEND_TRACKING_TASK:
        try:
            await _BACKEND_TRACKING_TASK
        except Exception:
            pass
        _BACKEND_TRACKING_TASK = None
    return {'ok': True, 'message': 'Inseguimento fermato.'}


@app.post('/api/tracking/apriltag-step')
async def tracking_apriltag_step(payload: dict[str, Any] = Body(default_factory=dict)):
    global _BACKEND_TRACKING_ACTIVE, _LAST_TRACKING_STATUS
    if _BACKEND_TRACKING_ACTIVE:
        return {
            'ok': True,
            **_LAST_TRACKING_STATUS
        }
        
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
    far_search = bool(payload.get('far_search', False))
    last_frame_id = int(payload.get('last_frame_id') or 0)
    result: dict[str, Any] = {'ok': False, 'target_id': target_id}
    try:
        started = time.perf_counter()
        target_dictionary, previous_tag, missed_frames = _TRACKER_STATE.snapshot(target_id, target_dictionary, previous_tag, missed_frames)
        live_detection = None
        stream_is_video = bool(stream_url and _is_video_stream_url(stream_url))
        if stream_is_video and not acquire_target and target_id is not None:
            _APRILTAG_LIVE_DETECTOR.configure(stream_url, {
                'target_id': target_id,
                'target_dictionary': target_dictionary or 'APRILTAG_25h9',
                'previous_tag': previous_tag,
                'max_detect_width': max_detect_width,
                'far_search': far_search,
            })
            live_detection = _APRILTAG_LIVE_DETECTOR.latest(min_frame_id=last_frame_id, max_age=0.15, wait_seconds=0.040)
        if live_detection is not None:
            detection = live_detection
            loaded = started
            detected = started + float(detection.get('perf', {}).get('total_ms') or 0.0) / 1000.0
        else:
            frame = await _load_tracking_frame(snapshot_url, stream_url, last_frame_id)
            loaded = time.perf_counter()
            detection = await asyncio.to_thread(
                    _detect_apriltags_from_frame,
                    frame,
                    target_id,
                    target_dictionary,
                    previous_tag if missed_frames == 0 else None,
                    acquire_target,
                    max_detect_width,
                    (not target_dictionary) and (acquire_target or (target_id is None and missed_frames >= 2)),
                    far_search,
                )
            detected = time.perf_counter()
        _TRACKER_STATE.update(target_id, detection['tag'])
        command = _compute_tag_tracking_command(detection['tag'], detection['width'], detection['height'], desired_size_ratio, max_linear, max_angular)
        perf = detection.get('perf') if isinstance(detection.get('perf'), dict) else None
        result.update({
            'ok': True,
            **detection,
            'command': command,
            'perf': perf or {
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


@app.post('/api/tracking/reset')
async def tracking_reset():
    global _BACKEND_TRACKING_ACTIVE, _BACKEND_TRACKING_TASK
    _BACKEND_TRACKING_ACTIVE = False
    if _BACKEND_TRACKING_TASK:
        try:
            await _BACKEND_TRACKING_TASK
        except Exception:
            pass
        _BACKEND_TRACKING_TASK = None
    _STREAM_FRAME_CACHE.stop()
    _APRILTAG_LIVE_DETECTOR.stop()
    _TRACKER_STATE.reset()
    return {'ok': True, 'message': 'Tracking e cache camera resettati'}


@app.get('/health')
def health():
    return {'ok': True}
