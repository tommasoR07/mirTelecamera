# Raspberry Camera Server

Questo pacchetto gira sul Raspberry Pi collegato a una webcam USB standard UVC.
Espone due endpoint:

- `/video_feed` -> stream MJPEG per la web app MiR
- `/snapshot.jpg` -> singolo frame JPEG

## Requisiti

- Raspberry Pi 4 o 5
- Raspberry Pi OS
- Webcam USB compatibile UVC
- Python 3.10+

## Installazione veloce

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn camera_server:app --host 0.0.0.0 --port 8081
```

## Test

Dal browser della stessa rete:

- `http://IP_RASPBERRY:8081/health`
- `http://IP_RASPBERRY:8081/video_feed`
- `http://IP_RASPBERRY:8081/snapshot.jpg`

## Variabili ambiente utili

- `CAMERA_DEVICE=0`
- `CAMERA_WIDTH=640`
- `CAMERA_HEIGHT=480`
- `CAMERA_FPS=15`
- `JPEG_QUALITY=80`

## Note

- Non collegare la webcam al MiR direttamente.
- Monta Raspberry e webcam sul robot ma falli lavorare come nodo separato in rete.
- La web app principale va configurata con l'URL dello stream in pagina Settings.
