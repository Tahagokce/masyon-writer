import asyncio
import json
import os
from contextlib import AsyncExitStack
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .process import process_environment


class BrowserClient:
    def __init__(self, config):
        self.config = config
        self.stack = AsyncExitStack()

    async def __aenter__(self):
        try:
            # Child errors can include browser state. Never persist raw stderr.
            self.stderr = self.stack.enter_context(open(os.devnull, "w"))
            params = StdioServerParameters(command=self.config.command[0], args=self.config.command[1:],
                                           env=process_environment(self.config.secret_names))
            read, write = await self.stack.enter_async_context(stdio_client(params, errlog=self.stderr))
            self.session = await self.stack.enter_async_context(ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=self.config.tool_timeout)))
            await asyncio.wait_for(self.session.initialize(), timeout=self.config.tool_timeout)
            listed = await self.session.list_tools()
            self.tools = [t.model_dump(by_alias=True, exclude_none=True) for t in listed.tools]
            required = {"poc_preflight", "poc_record_start", "poc_step_start", "poc_record_stop", "browser_assert"}
            if not required <= {t["name"] for t in self.tools}:
                raise RuntimeError("MCP command must start the fork-aware POC bridge, not a bare MCP server")
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *exc):
        await self.stack.aclose()

    async def call(self, name: str, arguments: dict):
        result = await self.session.call_tool(name, arguments)
        texts = [c.text for c in result.content if c.type == "text"]
        if len(texts) != 1:
            raise RuntimeError("Unexpected MCP bridge response")
        payload = json.loads(texts[0])
        if result.isError:
            payload["isError"] = True
        return payload
