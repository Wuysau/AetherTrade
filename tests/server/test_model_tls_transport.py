import asyncio
import socket
import ssl
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from httpcore._backends.auto import AutoBackend

from trading_system.server.errors import ApiError
from trading_system.server.model_client import request


def test_pinned_address_uses_verified_hostname_on_real_tls(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'models.example.com')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('models.example.com')]), critical=False)
        .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(cert_path, key_path)
    client_tls = ssl.create_default_context(cafile=str(cert_path))
    seen_sni, seen_hosts, pinned = [], [], []
    server_tls.set_servername_callback(lambda sock, name, context: seen_sni.append(name))

    async def run():
        async def handle(reader, writer):
            try:
                data = await reader.readuntil(b'\r\n\r\n')
                seen_hosts.extend(line for line in data.split(b'\r\n') if line.lower().startswith(b'host:'))
                body = b'{"data":[]}'
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                    + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, '127.0.0.1', 0, ssl=server_tls)
        port = server.sockets[0].getsockname()[1]
        loop = asyncio.get_running_loop()
        original_resolve = loop.getaddrinfo
        original_connect = AutoBackend.connect_tcp
        async def resolve(host, port, **kwargs):
            if host in ('models.example.com', 'wrong.example.com'):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', port))]
            return await original_resolve(host, port, **kwargs)
        async def connect(backend, host, port, **kwargs):
            pinned.append((host, port))
            assert host == '8.8.8.8' and port == server.sockets[0].getsockname()[1]
            return await original_connect(backend, '127.0.0.1', port, **kwargs)
        monkeypatch.setattr(loop, 'getaddrinfo', resolve)
        monkeypatch.setattr(AutoBackend, 'connect_tcp', connect)
        config = {'base_url': f'https://models.example.com:{port}/v1', 'protocol': 'openai_chat',
                  'timeout_seconds': 5, 'local_mode': False}
        try:
            async with httpx.AsyncClient(verify=client_tls, trust_env=False) as client:
                assert await request(client, config, None, 'GET', '/models') == {'data': []}
                assert seen_sni == ['models.example.com']
                assert seen_hosts == [f'Host: models.example.com:{port}'.encode()]
                with pytest.raises(ApiError) as error:
                    await request(client, dict(config, base_url=f'https://wrong.example.com:{port}/v1'),
                                  None, 'GET', '/models')
                assert error.value.code == 'MODEL_REQUEST_FAILED'
                assert seen_sni[-1] == 'wrong.example.com'
                assert len(seen_hosts) == 1
                assert len(pinned) == 2
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(run())
