from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import httpx


class MiRClientError(Exception):
    pass


class MiRClient:
    def __init__(self, host: str, username: str, password: str, timeout: float = 2.0) -> None:
        cleaned_host = host.strip().rstrip('/')
        if not cleaned_host:
            raise MiRClientError('Host MiR non configurato.')
        self.host = cleaned_host
        self.base_url = f'{cleaned_host}/api/v2.0.0'
        self.timeout = timeout
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': self._build_auth(username, password),
            'Accept-Language': 'en_US',
        }

    @staticmethod
    def _build_auth(username: str, password: str) -> str:
        if not username or not password:
            raise MiRClientError('Username o password mancanti.')
        digest = hashlib.sha256(password.encode('utf-8')).hexdigest()
        raw = f'{username}:{digest}'.encode('utf-8')
        return f'Basic {base64.b64encode(raw).decode("utf-8")}';

    def _url(self, path: str) -> str:
        return f'{self.base_url}/{path.lstrip("/")}'

    def _raw_url(self, path: str) -> str:
        return f'{self.host}/{path.lstrip("/")}'

    async def _request(self, method: str, path: str, *, raw: bool = False, **kwargs: Any) -> Any:
        url = self._raw_url(path) if raw else self._url(path)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(method, url, headers=self.headers, **kwargs)
        if response.status_code >= 400:
            detail = response.text.strip() or f'HTTP {response.status_code}'
            raise MiRClientError(f'{method} {path}: {detail}')
        if not response.content:
            return None
        content_type = response.headers.get('content-type', '')
        if 'json' in content_type:
            return response.json()
        return response.text

    async def get_status(self) -> dict:
        return await self._request('GET', 'status')

    async def get_status_compact(self) -> dict:
        return await self._request('GET', 'status')

    async def clear_errors(self) -> Any:
        return await self._request('PUT', 'status', json={'clear_error': True})

    async def resume_ready(self) -> Any:
        # Sulle API MiR più comuni state_id=3 riporta il robot in Ready/Start se era in pausa.
        return await self._request('PUT', 'status', json={'state_id': 3})

    async def pause_robot(self) -> Any:
        return await self._request('PUT', 'status', json={'state_id': 4})

    async def get_mission_groups(self) -> list[dict]:
        return await self._request('GET', 'mission_groups?sort_by=name,asc')

    async def get_missions(self, group_id: str) -> list[dict]:
        return await self._request('GET', f'mission_groups/{group_id}/missions?sort_by=name,asc')

    async def get_all_missions(self) -> list[dict]:
        return await self._request('GET', 'missions?sort_by=name,asc')

    async def get_queue(self) -> list[dict]:
        return await self._request('GET', 'mission_queue?sort_by=id,desc&limit=25')

    async def enqueue_mission_debug(self, mission_id: str, input_name: str = '', value: str = '') -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        bodies: list[dict[str, Any]] = []
        base: dict[str, Any] = {'mission_id': mission_id}
        if input_name and value:
            base['parameters'] = [{'input_name': input_name, 'value': value}]
        bodies.append(base)
        if not (input_name and value):
            bodies.append({'mission_id': mission_id, 'parameters': []})

        for body in bodies:
            try:
                result = await self._request('POST', 'mission_queue', json=body)
                attempts.append({'ok': True, 'body': body, 'result': result})
                return {'ok': True, 'attempts': attempts, 'result': result}
            except MiRClientError as exc:
                attempts.append({'ok': False, 'body': body, 'error': str(exc)})
        raise MiRClientError('Impossibile accodare missione. Tentativi: ' + json.dumps(attempts, ensure_ascii=False))

    async def enqueue_mission(self, mission_id: str, input_name: str = '', value: str = '') -> Any:
        return await self.enqueue_mission_debug(mission_id, input_name, value)

    async def clear_queue(self) -> Any:
        return await self._request('DELETE', 'mission_queue')

    async def get_maps(self) -> list[dict]:
        return await self._request('GET', 'maps?sort_by=name,asc')

    async def get_map_positions(self, map_id: str) -> list[dict]:
        return await self._request('GET', f'maps/{map_id}/positions?sort_by=name,asc')

    async def get_map_zones(self, map_id: str) -> list[dict]:
        try:
            return await self._request('GET', f'maps/{map_id}/zones?sort_by=name,asc')
        except MiRClientError as exc:
            if '404' in str(exc) or 'Not Found' in str(exc):
                return []
            raise


    async def set_active_map(self, map_id: str) -> Any:
        if not map_id:
            raise MiRClientError('Map ID mancante.')
        attempts = []
        candidates = [
            {'map_id': map_id},
            {'map_id': map_id, 'state_id': 3},
        ]
        for body in candidates:
            try:
                return await self._request('PUT', 'status', json=body)
            except MiRClientError as exc:
                attempts.append({'body': body, 'error': str(exc)[:300]})
        raise MiRClientError('Impossibile impostare la mappa attiva via API. Tentativi: ' + json.dumps(attempts, ensure_ascii=False))

    async def get_map_position_detail(self, map_id: str, position_id: str) -> dict:
        # Alcune versioni API restituiscono tutti i dati già nella lista positions;
        # altre espongono endpoint dedicati. Proviamo entrambi i formati più comuni.
        attempts = []
        for path in (f'maps/{map_id}/positions/{position_id}', f'positions/{position_id}'):
            try:
                data = await self._request('GET', path)
                if isinstance(data, dict):
                    return data
            except MiRClientError as exc:
                attempts.append({'path': path, 'error': str(exc)[:200]})
        positions = await self.get_map_positions(map_id)
        for pos in positions:
            if str(pos.get('guid')) == str(position_id):
                return pos
        raise MiRClientError('Dettagli posizione non trovati. Tentativi: ' + json.dumps(attempts, ensure_ascii=False))

    @staticmethod
    def _extract_pose(position: dict) -> dict[str, float]:
        def first_number(*keys: str, default: float = 0.0) -> float:
            for key in keys:
                value = position.get(key)
                if value is not None and value != '':
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        pass
            return default
        return {
            'x': first_number('x', 'pos_x', 'position_x'),
            'y': first_number('y', 'pos_y', 'position_y'),
            'orientation': first_number('orientation', 'theta', 'yaw', 'pos_theta'),
        }

    async def localize_to_position(self, map_id: str, position_id: str) -> Any:
        if not map_id or not position_id:
            raise MiRClientError('Map ID o Position ID mancante.')
        position = await self.get_map_position_detail(map_id, position_id)
        pose = self._extract_pose(position)
        attempts = []
        candidates = [
            {'map_id': map_id, 'position': pose},
            {'map_id': map_id, 'position': pose, 'state_id': 3},
            {'position': pose},
            {'map_id': map_id, 'position_id': position_id},
            {'position_id': position_id},
        ]
        for body in candidates:
            try:
                result = await self._request('PUT', 'status', json=body)
                return {'result': result, 'payload': body, 'position': position}
            except MiRClientError as exc:
                attempts.append({'body': body, 'error': str(exc)[:300]})
        raise MiRClientError('Il firmware MiR non ha accettato la localizzazione via API. Usa il sito MiR per la localizzazione manuale oppure verifica l endpoint API specifico. Tentativi: ' + json.dumps(attempts, ensure_ascii=False))

    async def get_registers(self) -> list[dict]:
        return await self._request('GET', 'registers?limit=50&offset=0')

    async def set_register(self, register_id: int, value: str) -> Any:
        return await self._request('POST', f'registers/{register_id}', json={'value': value})

    async def get_metrics(self) -> str:
        return await self._request('GET', 'metrics')

    async def batch_status_queue(self) -> Any:
        return await self._request(
            'POST',
            'batch',
            json={'requests': [{'url': '/status', 'method': 'GET'}, {'url': '/mission_queue?sort_by=id,desc&limit=5', 'method': 'GET'}]},
        )

    async def experimental_drive(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if not path:
            raise MiRClientError('Endpoint manual drive non configurato. Inseriscilo in Settings dopo averlo verificato nella API documentation del robot.')
        kwargs: dict[str, Any] = {}
        if body is not None:
            kwargs['json'] = body
        return await self._request(method.upper(), path, raw=True, **kwargs)


def render_drive_body(template: str, linear: float, angular: float, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    filled = template.replace('{{linear}}', f'{linear:.4f}').replace('{{angular}}', f'{angular:.4f}')
    if meta:
        for key, value in meta.items():
            filled = filled.replace(f'{{{{{key}}}}}', str(value))
    try:
        payload = json.loads(filled)
    except json.JSONDecodeError as exc:
        raise MiRClientError(f'Template body manual drive non valido: {exc}') from exc
    if not isinstance(payload, dict):
        raise MiRClientError('Il body manual drive deve produrre un oggetto JSON.')
    return payload
