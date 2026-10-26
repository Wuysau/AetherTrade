"""Explicit, loopback-only browser QA provider. Never imported by application code.

Run manually with `python tests/server/model_fixture_server.py --port 18083`.
Responses identify themselves as test data; no external model request exists.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Do not record provider credentials or request contents.

    def reply(self, status, data):
        encoded = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == '/v1/models':
            self.reply(200, {'data': [{'id': name} for name in (
                'browser-fixture-model', 'browser-fixture-buy-model', 'browser-fixture-sell-model')]})
        else:
            self.reply(404, {'error': 'fixture route not found'})

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16384:
                self.reply(413, {'error': 'fixture request too large'})
                return
            body = json.loads(self.rfile.read(length))
            if self.path == '/v1/responses':
                context = json.loads(body['input'])
            elif self.path in ('/v1/chat/completions', '/v1/messages'):
                context = json.loads(body['messages'][-1]['content'])
            else:
                self.reply(404, {'error': 'fixture route not found'})
                return
            actions = {'browser-fixture-model': 'HOLD', 'browser-fixture-buy-model': 'BUY', 'browser-fixture-sell-model': 'SELL'}
            action = actions.get(body.get('model'))
            if action is None:
                self.reply(404, {'error': 'unknown controlled fixture model'})
                return
            text = json.dumps({'symbol': context['symbol'], 'action': action,
                'summary': f'浏览器联调测试响应（{action}）：此记录来自隔离的本机测试服务，仅验证软件流程。',
                'counter_evidence': ['测试服务不作投资判断', '仅用于验证配置、协议和持久化链路'],
                'invalidation': '非投资分析；仅作为功能验收记录'}, ensure_ascii=False)
            if self.path == '/v1/responses':
                result = {'status': 'completed', 'output': [{'type': 'message', 'status': 'completed', 'content': [{'type': 'output_text', 'text': text}]}]}
            elif self.path == '/v1/messages':
                result = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': text}]}
            else:
                result = {'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]}
            result['usage'] = {'input_tokens': 50, 'output_tokens': 80}
            self.reply(200, result)
        except (KeyError, ValueError, TypeError):
            self.reply(400, {'error': 'invalid fixture request'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18083)
    args = parser.parse_args()
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
