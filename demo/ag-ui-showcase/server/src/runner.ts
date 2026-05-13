/**
 * Spawn a Python framework runner subprocess and let it write canonical
 * events to KurrentDB. The TS server doesn't read the subprocess output
 * for content — it lets the runner write to KurrentDB and observes via
 * DEV-1559 in the per-lane route.
 *
 * Why subprocess + KurrentDB intermediation: the three Python
 * frameworks (MAF / ADK / Strands) can't share a venv (otel pin
 * conflict, see CLAUDE.md). Each lane has its own pyproject.toml at
 * ``runners/{lane}/`` and gets its own venv via ``uv``.
 */

import { spawn, type ChildProcess } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { existsSync } from 'node:fs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHOWCASE_ROOT = path.resolve(HERE, '..', '..');
const RUNNERS_DIR = path.join(SHOWCASE_ROOT, 'runners');

export type NativeFramework = 'maf' | 'adk' | 'strands';

export interface RunnerStartArgs {
  framework: NativeFramework;
  sessionId: string;
  userMessage: string;
  connectionString: string;
  dummyMode: boolean;
  anthropicApiKey?: string;
  logger?: (line: string) => void;
}

export interface RunnerHandle {
  process: ChildProcess;
  done: Promise<number>;
}

export function startRunner(args: RunnerStartArgs): RunnerHandle {
  const projectDir = path.join(RUNNERS_DIR, args.framework);
  const scriptPath = path.join(projectDir, 'runner.py');
  const log = args.logger ?? (() => undefined);

  const env: NodeJS.ProcessEnv = {
    ...process.env,
    KURRENTDB_CONNECTION_STRING: args.connectionString,
    DUMMY_MODE: args.dummyMode ? '1' : '',
  };
  if (args.anthropicApiKey) env.ANTHROPIC_API_KEY = args.anthropicApiKey;

  const uvPath = resolveUvPath();
  log(
    `[runner:${args.framework}] spawn ${uvPath} run --project ${projectDir} python ${scriptPath}`,
  );
  const child = spawn(
    uvPath,
    ['run', '--project', projectDir, 'python', scriptPath],
    {
      cwd: SHOWCASE_ROOT,
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
      shell: false,
    },
  );
  child.on('error', (err) => {
    log(`[runner:${args.framework}] spawn error: ${(err as Error).message}`);
  });
  child.on('exit', (code, signal) => {
    log(`[runner:${args.framework}] exit code=${code} signal=${signal}`);
  });

  child.stdout.on('data', (chunk: Buffer) => {
    log(`[runner:${args.framework}:stdout] ${chunk.toString().trimEnd()}`);
  });
  child.stderr.on('data', (chunk: Buffer) => {
    log(`[runner:${args.framework}:stderr] ${chunk.toString().trimEnd()}`);
  });

  child.stdin.write(JSON.stringify({ session_id: args.sessionId, user_message: args.userMessage }));
  child.stdin.end();

  const done = new Promise<number>((resolve) => {
    child.once('close', (code) => resolve(code ?? 0));
  });

  return { process: child, done };
}

function resolveUvPath(): string {
  const exts = process.platform === 'win32' ? ['.exe', '.cmd', '.bat', ''] : [''];
  const pathDirs = (process.env.PATH ?? '').split(path.delimiter);
  for (const dir of pathDirs) {
    if (!dir) continue;
    for (const ext of exts) {
      const candidate = path.join(dir, `uv${ext}`);
      if (existsSync(candidate)) return candidate;
    }
  }
  throw new Error(
    "Could not find 'uv' on PATH. Install: https://docs.astral.sh/uv/getting-started/installation/",
  );
}
