"""直接调用 ASGI HTTP 接口，验证 JSON 序列化，无需 httpx 或真实监听端口。"""
import asyncio
from dataclasses import dataclass
import json


@dataclass
class Response:
    status_code: int
    body: bytes

    def json(self):
        return json.loads(self.body)


def post_json(app, path, payload):
    async def request():
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        messages = []
        consumed = False

        async def receive():
            nonlocal consumed
            if consumed:
                return {'type': 'http.disconnect'}
            consumed = True
            return {'type': 'http.request', 'body': body, 'more_body': False}

        async def send(message):
            messages.append(message)

        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                 'method': 'POST', 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
                 'query_string': b'', 'root_path': '', 'server': ('testserver', 80),
                 'client': ('127.0.0.1', 8000), 'headers': [
                     (b'content-type', b'application/json'), (b'content-length', str(len(body)).encode())]}
        await app(scope, receive, send)
        status = next(message['status'] for message in messages if message['type'] == 'http.response.start')
        content = b''.join(message.get('body', b'') for message in messages if message['type'] == 'http.response.body')
        return Response(status, content)
    return asyncio.run(request())
