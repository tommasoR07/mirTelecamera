from __future__ import annotations

import asyncio
import json
from typing import Any

from . import db
from .mir_client import MiRClient, MiRClientError, render_drive_body

SUPPORTED_WORKFLOW_STEPS = {
    'enqueue_mission',
    'clear_errors',
    'set_register',
    'wait_seconds',
    'drive_command',
}


def get_client() -> MiRClient:
    settings = db.get_settings()
    return MiRClient(
        host=settings.get('host', ''),
        username=settings.get('username', ''),
        password=settings.get('password', ''),
    )


async def execute_drive_command(linear: float, angular: float, source: str = 'manual') -> dict[str, Any]:
    settings = db.get_settings()
    client = get_client()

    # 1) Usa endpoint configurato dall'utente, se presente.
    if settings.get('drive_endpoint_path', '').strip():
        body = render_drive_body(
            settings.get('drive_body_template', ''),
            linear=linear,
            angular=angular,
            meta={'source': source},
        )
        result = await client.experimental_drive(
            method=settings.get('drive_http_method', 'POST'),
            path=settings.get('drive_endpoint_path', ''),
            body=body,
        )
        return {
            'ok': True,
            'mode': 'configured',
            'endpoint': settings.get('drive_endpoint_path', ''),
            'method': settings.get('drive_http_method', 'POST'),
            'payload': body,
            'result': result,
        }

    # 2) Fallback sperimentale: prova formati comuni.
    # Non tutti i MiR li supportano: se falliscono viene restituito un errore diagnostico chiaro.
    attempts: list[dict[str, Any]] = []
    candidates = [
        ('POST', 'api/v2.0.0/robots/1/move', {'velocity': {'linear': linear, 'angular': angular}}),
        ('POST', 'api/v2.0.0/robots/1/move', {'linear': linear, 'angular': angular}),
        ('POST', 'api/v2.0.0/robots/0/move', {'velocity': {'linear': linear, 'angular': angular}}),
        ('POST', 'api/v2.0.0/robots/0/move', {'linear': linear, 'angular': angular}),
    ]
    for method, path, body in candidates:
        try:
            result = await client.experimental_drive(method=method, path=path, body=body)
            return {'ok': True, 'mode': 'auto-fallback', 'endpoint': path, 'method': method, 'payload': body, 'result': result}
        except Exception as exc:
            attempts.append({'endpoint': path, 'method': method, 'payload': body, 'error': str(exc)[:300]})
    raise MiRClientError('Nessun endpoint manual drive configurato e fallback non riusciti. Configura Drive endpoint in Settings. Tentativi: ' + json.dumps(attempts, ensure_ascii=False))


async def execute_manual_action(action: str) -> dict[str, Any]:
    """Attiva/rilascia controllo manuale dalla web app.

    Se l'utente ha configurato un endpoint manuale in Settings viene usato quello.
    Altrimenti vengono provati alcuni comandi sicuri/diagnostici verso /status.
    """
    settings = db.get_settings()
    client = get_client()
    if action not in {'take', 'release', 'pause', 'ready', 'stop'}:
        raise MiRClientError(f'Azione manuale non supportata: {action}')

    if action == 'pause':
        result = await client.pause_robot()
        return {'ok': True, 'action': action, 'endpoint': 'api/v2.0.0/status', 'payload': {'state_id': 4}, 'result': result}
    if action == 'ready' or action == 'release':
        # Se release configurato, usalo. Altrimenti usa Ready.
        if action == 'release' and settings.get('manual_release_endpoint_path', '').strip():
            body = render_drive_body(settings.get('manual_release_body_template', '{}'), 0.0, 0.0, {'source': 'manual-release'})
            result = await client.experimental_drive(settings.get('manual_release_http_method', 'PUT'), settings.get('manual_release_endpoint_path', ''), body)
            return {'ok': True, 'action': action, 'mode': 'configured', 'payload': body, 'result': result}
        result = await client.resume_ready()
        return {'ok': True, 'action': action, 'endpoint': 'api/v2.0.0/status', 'payload': {'state_id': 3}, 'result': result}
    if action == 'stop':
        # Stop software: invia velocity zero al drive adapter, poi mette pausa.
        stop_result = None
        try:
            stop_result = await execute_drive_command(0.0, 0.0, 'manual-stop')
        except Exception as exc:
            stop_result = {'ok': False, 'warning': str(exc)[:300]}
        pause_result = await client.pause_robot()
        return {'ok': True, 'action': action, 'drive_stop': stop_result, 'pause_result': pause_result}

    # action == take
    if settings.get('manual_take_endpoint_path', '').strip():
        body = render_drive_body(settings.get('manual_take_body_template', '{}'), 0.0, 0.0, {'source': 'manual-take'})
        result = await client.experimental_drive(settings.get('manual_take_http_method', 'PUT'), settings.get('manual_take_endpoint_path', ''), body)
        return {'ok': True, 'action': action, 'mode': 'configured', 'payload': body, 'result': result}

    # Fallback: mette il robot in Ready e prova payload mode_id comuni su /status.
    attempts: list[dict[str, Any]] = []
    candidates = [
        ('PUT', 'api/v2.0.0/status', {'state_id': 3}),
        ('PUT', 'api/v2.0.0/status', {'mode_id': 1}),
        ('PUT', 'api/v2.0.0/status', {'mode_id': 2}),
        ('PUT', 'api/v2.0.0/status', {'mode_text': 'Manual'}),
    ]
    for method, path, body in candidates:
        try:
            result = await client.experimental_drive(method=method, path=path, body=body)
            return {'ok': True, 'action': action, 'mode': 'auto-fallback', 'endpoint': path, 'method': method, 'payload': body, 'result': result}
        except Exception as exc:
            attempts.append({'endpoint': path, 'method': method, 'payload': body, 'error': str(exc)[:300]})
    raise MiRClientError('Non sono riuscito ad attivare il controllo manuale. Inserisci in Settings l endpoint manuale trovato dal sito MiR. Tentativi: ' + json.dumps(attempts, ensure_ascii=False))


async def execute_workflow(workflow_id: int) -> list[str]:
    workflow = db.get_workflow(workflow_id)
    if not workflow:
        raise MiRClientError('Workflow non trovato.')

    client = get_client()
    logs: list[str] = []
    for index, step in enumerate(workflow['steps'], start=1):
        step_type = step.get('type', '').strip()
        logs.append(f'Step {index}: {step_type}')
        if step_type == 'enqueue_mission':
            await client.enqueue_mission(
                mission_id=step.get('mission_id', ''),
                input_name=step.get('input_name', ''),
                value=step.get('value', ''),
            )
            logs.append('  missione accodata')
        elif step_type == 'clear_errors':
            await client.clear_errors()
            logs.append('  errori resettati')
        elif step_type == 'set_register':
            await client.set_register(int(step.get('register_id')), str(step.get('value', '0')))
            logs.append('  registro aggiornato')
        elif step_type == 'wait_seconds':
            seconds = float(step.get('seconds', 1))
            logs.append(f'  attesa {seconds} s')
            await asyncio.sleep(seconds)
        elif step_type == 'drive_command':
            linear = float(step.get('linear', 0.0))
            angular = float(step.get('angular', 0.0))
            await execute_drive_command(linear=linear, angular=angular, source='workflow')
            logs.append(f'  drive command inviato linear={linear} angular={angular}')
        else:
            logs.append(f'  step ignorato: tipo non supportato ({step_type})')
    return logs


def parse_steps_json(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f'JSON non valido: {exc}') from exc
    if not isinstance(data, list):
        raise ValueError('steps_json deve essere una lista JSON.')
    for item in data:
        if not isinstance(item, dict):
            raise ValueError('Ogni step deve essere un oggetto JSON.')
        step_type = str(item.get('type', '')).strip()
        if step_type and step_type not in SUPPORTED_WORKFLOW_STEPS:
            raise ValueError(f'Tipo step non supportato: {step_type}')
    return data
