"""Actual pinned SDK sign/encode probe with an in-process fake HTTP transport.

No real key, account discovery, DNS/network request or external endpoint is used.
The generated HMAC uses a public test string and proves serialization only.
"""
import argparse
import asyncio
from hashlib import sha256
import hmac
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import parse_qsl,urlencode,urlsplit

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src'))

from trading_system.contracts import types as t
from trading_system.storage.models import decode
from trading_system.execution.creation_requests import VenueCreationRequest
from trading_system.engine.nautilus.native_creation import creation_call
from trading_system.engine.nautilus.sdk_pins import inspect_installed_sdk


async def probe(items):
    build=inspect_installed_sdk()
    from nautilus_trader.adapters.binance.http.client import BinanceHttpClient
    from nautilus_trader.common.component import LiveClock
    from nautilus_trader.core.nautilus_pyo3 import HttpMethod
    public_test_secret='OFFLINE-PUBLIC-SHAPE-FIXTURE-NOT-AN-ACCOUNT-SECRET'
    class InProcessFakeHttp:
        def __init__(self):self.requests=[]
        async def request(self,http_method,*,url,headers,body,keys):
            assert http_method==HttpMethod.POST and url.startswith('https://offline.invalid/') and body is None
            self.requests.append(dict(method='POST',url=url,ratelimiter_keys=keys))
            return SimpleNamespace(status=200,body=b'{"offline_shape_probe":true}',headers={})
    client=BinanceHttpClient(clock=LiveClock(),api_key='OFFLINE-PUBLIC-SHAPE-FIXTURE',api_secret=public_test_secret,
        base_url='https://offline.invalid')
    wire=InProcessFakeHttp();client._client=wire
    results=[]
    for item in items:
        request=decode(VenueCreationRequest,item['request']);clock=decode(t.ClockReading,item['clock'])
        call=creation_call(request,clock=clock,recv_window_ms=999)
        before=tuple(call.parameters)
        payload=dict(call.parameters)
        keys=['binance:'+call.endpoint.value,'binance:global']
        raw=await BinanceHttpClient.sign_request(client,HttpMethod.POST,call.endpoint.value,payload=payload,ratelimiter_keys=keys)
        assert raw==b'{"offline_shape_probe":true}' and tuple(call.parameters)==before
        observed=parse_qsl(urlsplit(wire.requests[-1]['url']).query)
        signature=hmac.new(public_test_secret.encode(),urlencode(dict(before)).encode(),sha256).hexdigest()
        assert observed==[*before,('signature',signature)]
        assert wire.requests[-1]['ratelimiter_keys']==keys
        results.append(dict(command_id=call.command_id,namespace=call.namespace.value,endpoint=call.endpoint.value,
            original_parameters=[list(p) for p in call.original_parameters],request_hash=call.request_hash,
            exact_sdk_signature_and_wire_shape=True))
    return dict(build=build,actual_sdk_client_constructed=True,http_transport='IN_PROCESS_FAKE_NO_NETWORK',
        actual_account_or_endpoint_called=False,permission_or_consumption_proof=False,
        native_order_qualification='NOT_RUN',results=results)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--fixtures',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();items=json.loads(Path(args.fixtures).read_text())
    result=asyncio.run(probe(items));Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    print(str(len(result['results']))+' actual pinned SDK signed wire shapes matched; fake transport only; native qualification NOT_RUN')


if __name__=='__main__':main()
