"""Real stdio MCP/fork check using only a local fixture; no Gemini request."""
import asyncio
import json

from src.config import Config
from src.mcp.client import BrowserClient
from src.security.secrets import Secrets


async def check():
    config = Config.from_env()
    async with BrowserClient(config) as browser:
        result = await browser.call('poc_preflight', {})
        print(json.dumps(Secrets(config.secret_names).object(result), indent=2))
        return 0 if result.get('idFirstVerified') else 2


if __name__ == '__main__':
    try:
        raise SystemExit(asyncio.run(check()))
    except Exception as exc:
        print(f'BLOCKED: fork/MCP startup failed ({type(exc).__name__}). Check built fork and browser path.')
        raise SystemExit(2)
