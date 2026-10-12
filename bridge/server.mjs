import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { Runtime, loadFork } from './runtime.mjs';

const runtime = new Runtime(loadFork(process.env.PLAYWRIGHT_FORK_PATH));
const server = new Server({name:'jira-fork-playwright-mcp',version:'0.1.0'}, {capabilities:{tools:{}}});
let queue = Promise.resolve();
server.setRequestHandler(ListToolsRequestSchema, async () => ({tools:runtime.tools}));
server.setRequestHandler(CallToolRequestSchema, request => {
  const task = queue.then(async () => {
    try {
      const result = runtime.guard.object(await runtime.call(request.params.name, request.params.arguments || {}));
      return {isError:Boolean(result.isError), content:[{type:'text',text:JSON.stringify(result)}]};
    } catch (error) {
      return {isError:true, content:[{type:'text',text:JSON.stringify({isError:true,
        ...runtime.guard.diagnostic(error, request.params.name, runtime.pages.get(runtime.active)?.alias),
        pages:runtime.guard.object(runtime.pageState()), metrics:runtime.metrics(),
        logEvents:runtime.drainLogEvents()})}]};
    }
  });
  queue = task.catch(() => {});
  return task;
});
const shutdown = async () => { await runtime.close(); process.exit(0); };
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
process.stdin.on('end', shutdown);
try {
  await runtime.initialize();
  await server.connect(new StdioServerTransport());
} catch {
  await runtime.close();
  process.stderr.write('Fork MCP startup failed. Check built fork APIs and browser executable.\n');
  process.exitCode = 2;
}
