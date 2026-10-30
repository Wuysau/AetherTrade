"""Real SDK quota/HTTP recorder. Public fixtures only, no venue evidence."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, Timer, get_ident
from time import monotonic
import asyncio
import json
import socket
from nautilus_trader.core.nautilus_pyo3 import HttpClient, HttpMethod, Quota


def run():
    assert hasattr(HttpClient, 'request_guarded'), 'ACTUAL_PINNED_SDK_HAS_NO_POST_QUEUE_GUARD'
    records = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            records.append(self.path)
            if self.path.startswith('/reset'):
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(307 if self.path.startswith('/redirect') else 200)
            self.send_header('Location', '/must-not-follow')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'{}')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    owner_thread = get_ident()
    results = []
    @contextmanager
    def allow():
        assert get_ident() == owner_thread
        yield
        assert get_ident() == owner_thread
    def client():
        return HttpClient(keyed_quotas=[('original', Quota.rate_per_second(1))])
    def send(c, path, guard):
        return c.request_guarded(HttpMethod.POST, url + path, guard, keys=['original'])
    try:
        for fault in ('positive', 'expiry', 'revoked', 'callback_failure'):
            c = client(); send(c, '/warm', allow())
            start = monotonic(); before = len(records)
            authority = {'current': True}
            timer = Timer(.2, lambda: authority.update(current=False)); timer.start()
            checked = []
            @contextmanager
            def fresh_guard():
                assert get_ident() == owner_thread
                checked.append(monotonic() - start)
                assert checked[-1] >= .75, 'guard ran before real quota wait'
                if fault == 'expiry':
                    assert monotonic() < start + .2, 'ORIGINAL_WINDOW_EXPIRED'
                if fault == 'revoked':
                    assert authority['current'], 'CURRENT_AUTHORITY_REVOKED'
                if fault == 'callback_failure':
                    raise ValueError('CALLBACK_FAILURE')
                yield
            try:
                send(c, '/api/v3/order?newClientOrderId=original&quantity=1', fresh_guard())
                assert fault == 'positive'
            except (AssertionError, ValueError) as exc:
                if fault == 'positive': raise
                assert str(exc) == {'expiry':'ORIGINAL_WINDOW_EXPIRED',
                    'revoked':'CURRENT_AUTHORITY_REVOKED','callback_failure':'CALLBACK_FAILURE'}[fault]
            finally: timer.join()
            assert len(checked) == 1 and checked[0] >= .75
            count = len(records) - before
            assert count == int(fault == 'positive')
            results.append(dict(case=fault, elapsed=monotonic()-start, writes=count))
        for fault in ('reset', 'redirect'):
            before = len(records)
            try: send(client(), '/' + fault, allow())
            except Exception:
                if fault != 'reset': raise
            assert len(records) - before == 1
            results.append(dict(case=fault, writes=1, status='UNKNOWN', recovery='QUERY_ORIGINAL_IDS'))
        before = len(records)
        try: send(client(), '/missing', None)
        except Exception: pass
        else: raise AssertionError('missing guard accepted')
        assert len(records) == before
        @contextmanager
        def cancelled():
            raise asyncio.CancelledError('internal guard cancelled')
            yield
        before = len(records)
        try: send(client(), '/cancelled', cancelled())
        except asyncio.CancelledError: pass
        else: raise AssertionError('cancelled guard accepted')
        assert len(records) == before
        @contextmanager
        def nested_guard():
            try: send(client(), '/nested-must-not-send', allow())
            except ValueError as exc:
                assert 'GUARDED_NESTED_TOKIO_RUNTIME_REJECTED' in str(exc)
            else: raise AssertionError('nested Tokio runtime accepted')
            yield
        before = len(records)
        send(client(), '/outer', nested_guard())
        assert len(records) == before + 1
        async def existing_loop():
            # Synchronous call retains same owner OS thread even inside asyncio.
            send(client(), '/existing-loop', allow())
        asyncio.run(existing_loop())
        return dict(results=results, wire_paths=records, external_activation_allowed=False,
                    venue_evidence=False, actual_sdk_rate_limiter=True)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
        assert not thread.is_alive()

if __name__ == '__main__': print(json.dumps(run(), indent=2))
