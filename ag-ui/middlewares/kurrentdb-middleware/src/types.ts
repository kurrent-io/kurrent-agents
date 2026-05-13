/**
 * Canonical event payload types — hand-written stopgap for v1.
 *
 * MIRRORS schema/proto/kurrent/agent/v2/*.proto. Field names are
 * snake_case to match the wire JSON produced by the generated Python /
 * .NET schema packages. Acceptance test in `ag-ui/interop-tests/`
 * proves structural equivalence.
 *
 * TODO(schema-typescript): replace with generated types once
 * `schema/typescript/` lands (buf-gen-es from the same protos).
 * See ag-ui/GAPS.md §5.
 */

export interface CanonicalBase {
  /** ISO-8601 timestamp; required on every canonical event. */
  timestamp: string;
  /** Framework-specific extension envelope. */
  extensions?: Record<string, unknown>;
}

export interface ToolSpec {
  name: string;
  description?: string;
  input_schema?: unknown;
  source?: string;
}

export interface AgentConfig {
  tools?: ToolSpec[];
  plugins?: string[];
  conversation_manager?: Record<string, unknown>;
  model_parameters?: Record<string, unknown>;
}

export interface SessionStarted extends CanonicalBase {
  app_name?: string;
  agent_name?: string;
  model?: string;
  tenant_id?: string;
  user_id?: string;
  agent_config?: AgentConfig;
  previous_session_id?: string;
}

export interface SessionEnded extends CanonicalBase {
  reason?: string;
}

export interface UserMessageReceived extends CanonicalBase {
  content: string;
  message_id: string;
  author_name?: string;
  created_at: string;
}

export interface AssistantTextGenerated extends CanonicalBase {
  content: string;
  message_id: string;
  author_name?: string;
  created_at: string;
  message_index?: number;
}

export interface ToolCallInfo {
  call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
}

export interface AssistantToolCallsGenerated extends CanonicalBase {
  tool_calls: ToolCallInfo[];
  /** Optional carrier text emitted in the same assistant message. */
  content?: string;
  message_id: string;
  author_name?: string;
  created_at: string;
  message_index?: number;
}

export interface AssistantThinkingGenerated extends CanonicalBase {
  content?: string;
  encrypted?: boolean;
  signature?: string;
  message_id: string;
  author_name?: string;
  created_at: string;
  message_index?: number;
}

export interface ToolResultReceived extends CanonicalBase {
  call_id: string;
  tool_name: string;
  result: string;
  message_id: string;
  created_at: string;
  message_index?: number;
}

/** Discriminated union of canonical events the middleware can emit in v1. */
export type CanonicalEvent =
  | { type: 'SessionStarted'; payload: SessionStarted }
  | { type: 'SessionEnded'; payload: SessionEnded }
  | { type: 'UserMessageReceived'; payload: UserMessageReceived }
  | { type: 'AssistantTextGenerated'; payload: AssistantTextGenerated }
  | { type: 'AssistantToolCallsGenerated'; payload: AssistantToolCallsGenerated }
  | { type: 'AssistantThinkingGenerated'; payload: AssistantThinkingGenerated }
  | { type: 'ToolResultReceived'; payload: ToolResultReceived };

/** Schema version stamped on event metadata. Mirrors SCHEMA_v2 §9. */
export const SCHEMA_VERSION = 2 as const;

/** KurrentDB metadata keys. */
export const SCHEMA_VERSION_METADATA_KEY = '$schema_version';
export const RUN_ID_METADATA_KEY = '$run_id';
export const USAGE_METADATA_KEY = '$usage';
