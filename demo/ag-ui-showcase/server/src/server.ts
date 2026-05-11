/**
 * ag-ui-showcase Fastify server.
 *
 * Phase 2 hosts three native AG-UI lanes (each is a Python subprocess
 * that writes canonical events; the replay agent live-tails the
 * resulting AgentSession-* stream and emits AG-UI events back):
 *   /agent/maf      — Microsoft Agent Framework
 *   /agent/adk      — Google ADK
 *   /agent/strands  — Strands
 *
 * Env:
 *   PORT (default 7000)
 *   KURRENTDB_CONNECTION_STRING (default kurrentdb://localhost:2113?Tls=false)
 *   DUMMY_MODE (truthy: skip LLM, use canned responses)
 *   ANTHROPIC_API_KEY (when not in dummy mode)
 */

import Fastify from 'fastify';
import { KurrentDBClient } from '@kurrent/kurrentdb-client';

import { registerNativeRoute } from './routes/native.js';

const PORT = Number(process.env.PORT ?? 7000);
const CONN =
  process.env.KURRENTDB_CONNECTION_STRING ?? 'kurrentdb://localhost:2113?Tls=false';
const DUMMY = ['1', 'true', 'yes'].includes(
  (process.env.DUMMY_MODE ?? '').toLowerCase(),
);

async function main(): Promise<void> {
  const app = Fastify({
    logger: { level: 'info' },
  });

  app.addHook('onRequest', async (req, reply) => {
    reply.header('Access-Control-Allow-Origin', '*');
    reply.header('Access-Control-Allow-Headers', 'Content-Type');
    reply.header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
    if (req.method === 'OPTIONS') {
      // Must return the reply so Fastify stops processing — otherwise
      // the request continues into route matching and trips
      // "Reply was already sent".
      return reply.code(204).send();
    }
  });

  app.get('/health', async () => ({
    status: 'ok',
    dummyMode: DUMMY,
    kurrentdb: redactConnectionString(CONN),
    lanes: ['maf', 'adk', 'strands'],
  }));

  const client = KurrentDBClient.connectionString(CONN);
  const nativeConfig = {
    client,
    connectionString: CONN,
    dummyMode: DUMMY,
    anthropicApiKey: process.env.ANTHROPIC_API_KEY,
    logger: (line: string) => app.log.info(line),
  };
  registerNativeRoute(app, 'maf', nativeConfig);
  registerNativeRoute(app, 'adk', nativeConfig);
  registerNativeRoute(app, 'strands', nativeConfig);

  await app.listen({ port: PORT, host: '0.0.0.0' });
  app.log.info(
    `ag-ui-showcase server up — http://localhost:${PORT}/health (dummy=${DUMMY}; lanes: maf, adk, strands)`,
  );
}

/**
 * Strip credentials and query parameters from a KurrentDB connection
 * string before reflecting it back from `/health`. Hosted clusters or
 * test setups can carry tokens like `?Authorization=Basic ...` or
 * embed `user:pass@` in the URL; never echo those.
 */
function redactConnectionString(conn: string): string {
  try {
    const url = new URL(conn);
    let hostPort = url.host;
    if (!hostPort) hostPort = `${url.hostname}:${url.port}`;
    return `${url.protocol}//${hostPort}`;
  } catch {
    return '<redacted>';
  }
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error(err);
  process.exit(1);
});
