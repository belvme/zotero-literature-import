"""Interactive real MCP client for initial pairing, without a simulated extension.

Use only while the registered MCP server is not running. Quit before refreshing
the app's MCP connections, because both would otherwise bind the same port.
"""
import asyncio
import json
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stdin.reconfigure(encoding='utf-8')
    root = Path(__file__).resolve().parent
    params = StdioServerParameters(command=sys.executable, args=[str(root/'server.py')])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print('READY: v2 MCP WebSocket background listener on 127.0.0.1:23130; automatic private pairing', flush=True)
            print('Commands: status [URL] | save [URL] | pdf URL | download ID | auth-status | authorize | quit', flush=True)
            while True:
                line = (await asyncio.to_thread(sys.stdin.readline)).strip()
                if not line or line == 'quit':
                    break
                if line.startswith('capture '):
                    name, args = 'save_and_check_pdf', json.loads(line[8:])
                elif line == 'auth-status':
                    name, args = 'local_write_auth_status', {}
                elif line == 'authorize':
                    name, args = 'authorize_local_writes', {}
                elif line == 'status':
                    name, args = 'connector_status', {}
                elif line.startswith('status '):
                    name, args = 'connector_status', {'expected_url': line[7:]}
                elif line.startswith('save '):
                    name, args = 'save_current_page', {'expected_url': line[5:]}
                elif line == 'save':
                    name, args = 'save_current_page', {}
                elif line.startswith('pdf '):
                    name, args = 'download_current_pdf', {'expected_url': line[4:]}
                elif line.startswith('download '):
                    name, args = 'download_status', {'download_id': int(line[9:])}
                else:
                    print('Unknown command', flush=True)
                    continue
                result = await session.call_tool(name, args)
                print(json.dumps(result.model_dump(mode='json'), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
