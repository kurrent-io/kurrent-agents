/**
 * ag-ui-showcase Fastify server.
 *
 * Phase 1: hosts the /agent/maf route. Phase 2 adds /agent/adk,
 * /agent/strands, /agent/langgraph.
 *
 * Env:
 * - PORT (default 7000)
 * - KURRENTDB_CONNECTION_STRING (default kurrentdb://localhost:2113?Tls=false)
 * - DUMMY_MODE (truthy: skip LLM, use canned responses)
 * - ANTHROPIC_API_KEY (when not in dummy mode)
 */

import Fastify from 'fastify';
import { KurrentDBClient } from '@kurrent/kurrentdb-client';

import { registerMafRoute } from './routes/maf.js';

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

  // Browser is on a different origin (Vite dev :5173). Enable CORS for
  // the AG-UI SSE endpoint.
  app.addHook('onRequest', async (req, reply) => {
    reply.header('Access-Control-Allow-Origin', '*');
    reply.header('Access-Control-Allow-Headers', 'Content-Type');
    reply.header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
    if (req.method === 'OPTIONS') {
      reply.code(204).send();
    }
  });

  app.get('/health', async () => ({
    status: 'ok',
    dummyMode: DUMMY,
    kurrentdb: CONN,
  }));

  const client = KurrentDBClient.connectionString(CONN);

  registerMafRoute(app, {
    client,
    connectionString: CONN,
    dummyMode: DUMMY,
    anthropicApiKey: process.env.ANTHROPIC_API_KEY,
    logger: (line) => app.log.info(line),
  });

  await app.listen({ port: PORT, host: '0.0.0.0' });
  app.log.info(
    `ag-ui-showcase server up — http://localhost:${PORT}/health (dummy=${DUMMY})`,
  );
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error(err);
  process.exit(1);
});
