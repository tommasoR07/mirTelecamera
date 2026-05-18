# MiR Tracking System

Web app Python/FastAPI per MiR con tracking AprilTag usando un telefono come camera IP.

## Cosa serve

- PC, MiR e telefono sulla stessa rete.
- Un'app sul telefono che espone video in rete.
- Un AprilTag sulla pettorina.
- Le micro-missioni MiR gia create: `follow_avanti`, `follow_sinistra`, `follow_destra`, `follow_stop`.

## URL camera consigliati

Preferibile, se l'app del telefono lo supporta:

```text
Stream URL: http://IP_TELEFONO:8080/video
Snapshot URL: http://IP_TELEFONO:8080/shot.jpg
```

Con questa modalita il browser mostra anche l'anteprima live.

Se l'app fornisce RTSP o RTMP:

```text
Stream URL: rtsp://IP_TELEFONO:8554/live
Snapshot URL: lascia vuoto
```

oppure:

```text
Stream URL: rtmp://IP_TELEFONO/live
Snapshot URL: lascia vuoto
```

RTSP/RTMP non vengono mostrati direttamente dal browser, ma il backend li legge con OpenCV per riconoscere l'AprilTag.

## Avvio

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Poi apri:

```text
http://127.0.0.1:8000
```

## Uso

1. Avvia l'app camera sul telefono.
2. Copia lo stream URL in **Settings**.
3. Se disponibile, copia anche lo snapshot URL.
4. Vai in **Tracking**.
5. Premi **Connetti stream** solo se stai usando HTTP/MJPEG.
6. Premi **Riconosci AprilTag**.
7. Quando compare l'ID, premi **Segui AprilTag**.

## Note pratiche

- Per il telefono conviene usare HTTP/MJPEG se vuoi vedere l'anteprima nella pagina.
- RTSP/RTMP vanno bene per il riconoscimento, ma non per l'anteprima browser senza conversione.
- Se il riconoscimento e lento o instabile, abbassa risoluzione/FPS nell'app del telefono.
- Il follow non usa joystick manuale: accoda micro-missioni MiR stabili e distanziate nel tempo.
