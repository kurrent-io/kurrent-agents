/**
 * Spawn a Python framework runner subprocess and let it write canonical
 * events to KurrentDB. The TS server doesn't read the subprocess output
 * — it lets the runner write to KurrentDB and observes via DEV-1559.
 *
 * Why subprocess + KurrentDB intermediation rather than IPC: the three
 * Python frameworks (MAF / ADK / Strands) can't share a venv (otel pin
 * conflict). Each framework's runner has its own venv. The TS server
 * stays language-agnostic.
 */

import { spawn, type ChildProcess } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { existsSync } from 'node:fs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHOWCASE_ROOT = path.resolve(HERE, '..', '..');
const RUNNERS_DIR = path.join(SHOWCASE_ROOT, 'runners');

export interface RunnerStartArgs {
  /** Logical lane name — picks the runner script. */
  framework: 'maf' | 'adk' | 'strands';
  /** Canonical session_id (= AG-UI threadId). */
  sessionId: string;
  /** User's message for this turn. */
  userMessage: string;
  /** KurrentDB connection string passed to the subprocess. */
  connectionString: string;
  /** When set, runner writes canned events without an LLM. */
  dummyMode: boolean;
  /** ANTHROPIC_API_KEY (optional in dummy mode). */
  anthropicApiKey?: string;
  /** Optional extra logger. */
  logger?: (line: string) => void;
}

export interface RunnerHandle {
  process: ChildProcess;
  /** Resolves with exit code when subprocess terminates. */
  done: Promise<number>;
}

const RUNNER_SCRIPT: Record<RunnerStartArgs['framework'], string> = {
  maf: 'maf_runner.py',
  adk: 'adk_runner.py', // Phase 2
  strands: 'strands_runner.py', // Phase 2
};

export function startRunner(args: RunnerStartArgs): RunnerHandle {
  const script = RUNNER_SCRIPT[args.framework];
  const scriptPath = path.join(RUNNERS_DIR, script);
  const log = args.logger ?? (() => undefined);

  // Use `uv run` so each lane uses its scoped venv and dependencies.
  // The runners/ tree shares one venv for now (Phase 1 = MAF only);
  // Phase 2 splits per framework.
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    KURRENTDB_CONNECTION_STRING: args.connectionString,
    DUMMY_MODE: args.dummyMode ? '1' : '',
  };
  if (args.anthropicApiKey) env.ANTHROPIC_API_KEY = args.anthropicApiKey;

  // Resolve `uv` to an absolute path so spawn finds it without shell.
  // Node's spawn on Windows doesn't honour PATHEXT without shell:true,
  // and shell:true introduces cmd.exe quoting hazards (and was silently
  // exiting with code 0 here). Resolve the binary once and pass the
  // absolute path.
  const uvPath = resolveUvPath();
  log(`[runner:${args.framework}] spawn ${uvPath} run --project ${SHOWCASE_ROOT} python ${scriptPath}`);
  const child = spawn(uvPath, ['run', '--project', SHOWCASE_ROOT, 'python', scriptPath], {
    cwd: SHOWCASE_ROOT,
    env,
    stdio: ['pipe', 'pipe', 'pipe'],
    shell: false,
  });
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

  // Send the work payload on stdin and close it.
  child.stdin.write(JSON.stringify({ session_id: args.sessionId, user_message: args.userMessage }));
  child.stdin.end();

  const done = new Promise<number>((resolve) => {
    child.once('close', (code) => resolve(code ?? 0));
  });

  return { process: child, done };
}

/**
 * Resolve the `uv` executable to an absolute path. Walks PATH and
 * checks both `uv` (POSIX) and `uv.exe` (Windows). Throws if not found.
 */
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
