#!/usr/bin/env bash
set -e
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn camera_server:app --host 0.0.0.0 --port 8081
