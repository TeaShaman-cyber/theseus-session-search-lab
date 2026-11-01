import fs from 'node:fs';
import { makeRouterHandler } from '../../functions/router/handler.mjs';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));

const catalog = [{
  shard_id: 'fixture-0001',
  provider: 'fixture',
  generation_id: 'fixture-generation',
  url: 'https://fixture.invalid/search',
  state: 'READY',
}];

async function runMode(searchMode) {
  const dispatchModes = [];
  const handler = makeRouterHandler({
    catalog,
    fetchImpl: async (_url, options) => {
      const body = JSON.parse(options.body);
      dispatchModes.push(body.search_mode);
      const hits = body.search_mode === 'strict' ? input.strict_hits : input.recall_hits;
      return new Response(JSON.stringify({
        generation_id: 'fixture-generation',
        hits,
      }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    },
  });

  const response = await handler.fetch(new Request('https://router.invalid/mcp', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: searchMode,
      method: 'tools/call',
      params: {
        name: 'session_search',
        arguments: {
          query: input.query,
          scopes: ['dialogue', 'evidence'],
          limit: input.limit ?? 8,
          search_mode: searchMode,
        },
      },
    }),
  }));
  const payload = await response.json();
  if (payload?.result?.isError || !payload?.result?.structuredContent) {
    throw new Error(`MCP_BENCHMARK_FAILED_${searchMode}`);
  }
  return {
    result: payload.result.structuredContent,
    dispatch_modes: dispatchModes,
  };
}

const output = {
  strict: await runMode('strict'),
  recall: await runMode('recall'),
  auto: await runMode('auto'),
};

process.stdout.write(JSON.stringify(output));
