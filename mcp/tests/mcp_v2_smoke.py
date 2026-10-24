"""Real MCP + WebSocket + production JS. Browser API is a controlled mock."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from websockets.sync.client import connect

ROOT=Path(__file__).resolve().parents[1]

async def main():
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    test_url = f'ws://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
        token=secrets.token_urlsafe(32)
        token_file=Path(directory)/'token'
        token_file.write_text(token)
        params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'server.py')],
            env={**os.environ,'ZOTERO_MCP_TOKEN_FILE':str(token_file),'ZOTERO_MCP_WS_PORT':str(port)})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                tools={t.name for t in (await session.list_tools()).tools}
                assert tools=={'connector_status','save_current_page','download_current_pdf','download_status',
                              'local_write_auth_status','authorize_local_writes','save_and_check_pdf'}
                # No token and a web origin cannot impersonate the extension.
                try:
                    with connect(test_url,origin='https://evil.example',proxy=None):
                        raise AssertionError('Web origin accepted')
                except Exception as error:
                    assert not isinstance(error,AssertionError)
                with connect(test_url,origin='chrome-extension://'+'a'*32,proxy=None) as wrong:
                    wrong.send(json.dumps({'token':'wrong'}))
                    try: wrong.recv(timeout=2);raise AssertionError('Wrong token accepted')
                    except Exception as error: assert not isinstance(error,AssertionError)
                host=subprocess.Popen(['node',str(ROOT/'tests/extension_host.cjs')],
                    env={**os.environ,'TEST_BRIDGE_TOKEN':token,'TEST_BRIDGE_PORT':str(port)},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                try:
                    for _ in range(50):
                        status=await session.call_tool('connector_status')
                        if not status.isError: break
                        await asyncio.sleep(.1)
                    assert not status.isError, status
                    data=json.loads(status.content[0].text)
                    assert data['bridge_version']==2 and data['url']=='https://example.org/article'
                    assert not (await session.call_tool('save_current_page')).isError
                    result=await session.call_tool('download_current_pdf',{'expected_url':'https://example.org/article'})
                    assert not result.isError,result
                    ident=json.loads(result.content[0].text)['download_id']
                    done=await session.call_tool('download_status',{'download_id':ident})
                    assert json.loads(done.content[0].text)['state']=='complete'
                    assert (await session.call_tool('save_current_page',{'expected_url':'file:///secret'})).isError
                    assert (await session.call_tool('download_status',{'download_id':999})).isError
                    # Browser context survives local MCP restarts in production;
                    # test reconnect by restarting the controlled extension host.
                    host.terminate();host.wait(5)
                    await asyncio.sleep(.3)
                    host=subprocess.Popen(['node',str(ROOT/'tests/extension_host.cjs')],
                        env={**os.environ,'TEST_BRIDGE_TOKEN':token,'TEST_BRIDGE_PORT':str(port)},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                    for _ in range(50):
                        status=await session.call_tool('connector_status')
                        if not status.isError: break
                        await asyncio.sleep(.1)
                    assert not status.isError
                    print('PASS: real MCP + production JS + WebSocket auto-auth, no bridge page; status/save/download/reconnect; mock browser APIs')
                finally:
                    host.terminate();host.wait(5)

if __name__=='__main__': asyncio.run(main())
