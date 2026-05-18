from __future__ import annotations

import os
import threading
import time
from typing import Optional

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse

DEVICE = os.getenv("CAMERA_DEVICE", "0")
WIDTH = int(os.getenv("CAMERA_WIDTH", "640"))
HEIGHT = int(os.getenv("CAMERA_HEIGHT", "480"))
FPS = int(os.getenv("CAMERA_FPS", "15"))
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "80"))

app = FastAPI(title="Raspberry Camera Server")


class CameraService:
    def __init__(self) -> None:
        self.capture: Optional[cv2.VideoCapture] = None
        self.frame = None
        self.lock = threading.Lock()
        self.running = False
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.running:
            return
        dev = int(DEVICE) if DEVICE.isdigit() else DEVICE
        self.capture = cv2.VideoCapture(dev)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
        self.capture.set(cv2.CAP_PROP_FPS, FPS)
        if not self.capture.isOpened():
            raise RuntimeError(f"Impossibile aprire la camera: {DEVICE}")
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while self.running and self.capture is not None:
            ok, frame = self.capture.read()
            if ok:
                with self.lock:
                    self.frame = frame
            time.sleep(max(0.001, 1 / max(FPS, 1)))

    def get_jpeg(self) -> bytes:
        with self.lock:
            if self.frame is None:
                raise RuntimeError("Nessun frame disponibile")
            frame = self.frame.copy()
        ok, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ok:
            raise RuntimeError('Encoding JPEG fallito')
        return buf.tobytes()

    def stop(self) -> None:
        self.running = False
        if self.capture is not None:
            self.capture.release()


camera = CameraService()


@app.on_event('startup')
def startup() -> None:
    camera.start()


@app.on_event('shutdown')
def shutdown() -> None:
    camera.stop()


@app.get('/', response_class=HTMLResponse)
def index() -> str:
    return """
    <html><body style='font-family:sans-serif'>
    <h1>Raspberry Camera Server</h1>
    <p><a href='/video_feed'>Apri stream MJPEG</a></p>
    <p><a href='/snapshot.jpg'>Apri snapshot JPEG</a></p>
    </body></html>
    """


@app.get('/health')
def health() -> dict:
    return {'ok': True, 'device': DEVICE, 'width': WIDTH, 'height': HEIGHT, 'fps': FPS}


@app.get('/snapshot.jpg')
def snapshot() -> Response:
    try:
        return Response(content=camera.get_jpeg(), media_type='image/jpeg')
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get('/video_feed')
def video_feed() -> StreamingResponse:
    def generate():
        boundary = b'--frame\r\n'
        while True:
            try:
                jpg = camera.get_jpeg()
            except Exception:
                time.sleep(0.1)
                continue
            yield boundary
            yield b'Content-Type: image/jpeg\r\n\r\n' + jpg + b'\r\n'
            time.sleep(max(0.001, 1 / max(FPS, 1)))

    return StreamingResponse(generate(), media_type='multipart/x-mixed-replace; boundary=frame')
