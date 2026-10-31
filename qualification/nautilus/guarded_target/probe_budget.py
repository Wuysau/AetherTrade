"""RED on candidate1: the original quota wait must have an overall deadline."""
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
from time import monotonic
from nautilus_trader.core.nautilus_pyo3 import HttpClient,HttpMethod,Quota

count=[]
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        count.append(self.path);self.send_response(200);self.send_header('Content-Length','2')
        self.end_headers();self.wfile.write(b'{}')
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=Thread(target=server.serve_forever,daemon=True);thread.start()
try:
    client=HttpClient(keyed_quotas=[('slow',Quota.rate_per_minute(1))])
    url=f'http://127.0.0.1:{server.server_port}/original'
    client.request_guarded(HttpMethod.POST,url,nullcontext(),keys=['slow'])
    start=monotonic()
    try:client.request_guarded(HttpMethod.POST,url,nullcontext(),keys=['slow'],budget_ms=100)
    except Exception as exc:assert 'GUARDED_ORIGINAL_BUDGET_EXPIRED' in str(exc),repr(exc)
    else:raise AssertionError('quota timeout accepted')
    assert len(count)==1 and monotonic()-start < .75
    print({'case':'actual_minute_quota_overall_budget','writes_after_warm':0,'elapsed':monotonic()-start})
finally:
    server.shutdown();server.server_close();thread.join(timeout=3)
