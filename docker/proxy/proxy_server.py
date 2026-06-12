#!/usr/bin/env python3
"""
Minimal authenticated HTTP CONNECT proxy.

Designed to run on PHATT-RAID so that 2Captcha can solve DataDome challenges
through the same Breezeline residential IP used by the obsessed scraper.

Usage:
    PROXY_PORT=8888 PROXY_USER=proxyuser PROXY_PASS=<secret> python proxy_server.py

Docker:
    docker run -d --name dd-proxy --network phattvip \
        -p 8888:8888 \
        -e PROXY_USER=proxyuser \
        -e PROXY_PASS=<secret> \
        python:3.12-slim \
        sh -c "python /proxy_server.py"

    (Or use the Dockerfile in this directory for a self-contained image.)

Security:
    - Basic Proxy-Authorization header required (407 otherwise)
    - Only HTTP CONNECT tunnelling is supported (no plain-text relay)
    - Bind to 0.0.0.0 so Docker port-forwards work; restrict inbound via router
      firewall to 2Captcha solver IP ranges if paranoid

2Captcha proxy parameter for DataDome:
    DATADOME_SOLVE_PROXY=proxyuser:<secret>@23.245.109.252:8888
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dd-proxy")

HOST = "0.0.0.0"
PORT = int(os.environ.get("PROXY_PORT", "8888"))
USER = os.environ.get("PROXY_USER", "").strip()
PASS = os.environ.get("PROXY_PASS", "").strip()

_CRED_B64 = base64.b64encode(f"{USER}:{PASS}".encode()).decode() if USER and PASS else None

_CONNECT_RE = __import__("re").compile(
    rb"^CONNECT\s+([^:\s]+):(\d+)\s+HTTP/1\.\d\r?\n", __import__("re").IGNORECASE
)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10.0)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError):
        writer.close()
        return

    lines = head.split(b"\r\n")
    request_line = lines[0]

    m = _CONNECT_RE.match(request_line + b"\r\n")
    if not m:
        writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
        await writer.drain()
        writer.close()
        return

    target_host = m.group(1).decode()
    target_port = int(m.group(2))

    # Authentication
    if _CRED_B64 is not None:
        auth_ok = False
        for line in lines[1:]:
            if line.lower().startswith(b"proxy-authorization:"):
                scheme, _, creds = line[len(b"proxy-authorization:"):].strip().partition(b" ")
                if scheme.lower() == b"basic" and creds.decode().strip() == _CRED_B64:
                    auth_ok = True
                    break
        if not auth_ok:
            writer.write(
                b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                b"Proxy-Authenticate: Basic realm=\"dd-proxy\"\r\n"
                b"\r\n"
            )
            await writer.drain()
            writer.close()
            log.warning("auth fail from %s", peer)
            return

    try:
        remote_reader, remote_writer = await asyncio.wait_for(
            asyncio.open_connection(target_host, target_port), timeout=15.0
        )
    except (OSError, asyncio.TimeoutError) as e:
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await writer.drain()
        writer.close()
        log.warning("connect %s:%d failed: %s", target_host, target_port, e)
        return

    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    log.info("CONNECT %s:%d from %s", target_host, target_port, peer)

    await asyncio.gather(
        _pipe(reader, remote_writer),
        _pipe(remote_reader, writer),
    )


async def main() -> None:
    if not USER or not PASS:
        log.warning("PROXY_USER / PROXY_PASS not set — proxy will accept unauthenticated connections!")
    server = await asyncio.start_server(handle, HOST, PORT)
    log.info("dd-proxy listening on %s:%d (auth=%s)", HOST, PORT, bool(_CRED_B64))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
