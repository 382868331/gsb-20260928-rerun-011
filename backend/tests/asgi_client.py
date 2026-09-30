import asyncio
import json


class ASGIResponse:
    def __init__(self, status, headers, body):
        self.status_code = status
        self.headers = headers
        self._body = body

    def json(self):
        return json.loads(self._body.decode('utf-8'))


async def _send(app, method, path, payload=None):
    raw_body = json.dumps(payload).encode('utf-8') if payload is not None else b''
    scope = {
        'type': 'http',
        'asgi': {'version': '3.0'},
        'http_version': '1.1',
        'method': method,
        'scheme': 'http',
        'path': path,
        'raw_path': path.encode('utf-8'),
        'query_string': b'',
        'headers': [
            (b'content-type', b'application/json'),
            (b'content-length', str(len(raw_body)).encode()),
        ],
        'client': ('testclient', 50000),
        'server': ('testserver', 80),
    }
    received = {'value': False}

    async def receive():
        if not received['value']:
            received['value'] = True
            return {'type': 'http.request', 'body': raw_body}
        return {'type': 'http.disconnect'}

    response = {}

    async def send(message):
        if message['type'] == 'http.response.start':
            response['status'] = message['status']
            response['headers'] = message.get('headers', [])
            response['body'] = b''
        elif message['type'] == 'http.response.body':
            response['body'] += message.get('body', b'')

    await app(scope, receive, send)
    return ASGIResponse(response['status'], response['headers'], response['body'])


class SimpleClient:
    def __init__(self, app):
        self.app = app

    def _call(self, method, path, payload=None):
        return asyncio.run(_send(self.app, method, path, payload))

    def get(self, path):
        return self._call('GET', path)

    def post(self, path, json=None):
        return self._call('POST', path, json)

    def put(self, path, json=None):
        return self._call('PUT', path, json)

    def delete(self, path):
        return self._call('DELETE', path)
