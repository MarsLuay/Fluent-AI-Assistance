import { PROTOCOL_IR_OPERATIONS, type ProtocolIrOperation } from "./protocolIrContract";

export const PROTOCOL_IR_OFFLINE_MOTION_OPERATIONS = [
  "move_axis_command",
  "start_move_command",
  "wait_for_async_response"
] as const satisfies readonly ProtocolIrOperation[];

export function isProtocolIrOperation(value: unknown): value is ProtocolIrOperation {
  return typeof value === "string" && (PROTOCOL_IR_OPERATIONS as readonly string[]).includes(value);
}

export function isOfflineMotionOperation(value: unknown): boolean {
  return typeof value === "string" && (PROTOCOL_IR_OFFLINE_MOTION_OPERATIONS as readonly string[]).includes(value);
}

export function protocolIrOperationValidationMessage(operation: unknown): string | null {
  if (typeof operation !== "string" || !operation.trim()) return "Operation is empty.";
  const normalized = operation.trim();
  return isProtocolIrOperation(normalized)
    ? null
    : `Operation "${normalized}" is not registered in canonical Protocol IR.`;
}
