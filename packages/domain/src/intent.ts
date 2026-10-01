/**
 * Communication Intent domain types shared by mobile app, enterprise SDK and portal.
 * Values mirror contracts/openapi/common.yaml and contracts/state-machines/intent.yaml;
 * test/parity.test.ts fails on any drift.
 *
 * Server is authoritative for every transition. Clients use `canTransition` only to decide
 * which actions to show; they never apply transitions locally.
 */

export const INTENT_STATUSES = [
  "DRAFT",
  "REQUESTED",
  "DELIVERED",
  "ACCEPTED",
  "RESCHEDULED",
  "DECLINED",
  "SCHEDULED",
  "IN_PROGRESS",
  "COMPLETED",
  "FOLLOW_UP_REQUIRED",
  "CANCELLED",
  "EXPIRED",
] as const;
export type IntentStatus = (typeof INTENT_STATUSES)[number];

export const PRIORITIES = ["LOW", "NORMAL", "IMPORTANT", "URGENT"] as const;
export type Priority = (typeof PRIORITIES)[number];

export const INTENT_SOURCES = ["DECLARED", "PREDICTED", "UNKNOWN"] as const;
export type IntentSource = (typeof INTENT_SOURCES)[number];

export const VERIFICATION_LEVELS = ["NONE", "PHONE", "ORG", "ORG_AGENT_NUMBER"] as const;
export type VerificationLevel = (typeof VERIFICATION_LEVELS)[number];

export const CHANNELS = ["VOIP", "SDK", "PRECALL_PUSH", "PSTN", "PLATFORM_CALLER_ID"] as const;
export type Channel = (typeof CHANNELS)[number];

export const ACTORS = ["CALLER", "RECEIVER", "SYSTEM"] as const;
export type Actor = (typeof ACTORS)[number];

export const RESPONSE_ACTIONS = ["ACCEPT", "LATER", "PROPOSE", "SCHEDULE", "MESSAGE", "DECLINE"] as const;
export type ResponseAction = (typeof RESPONSE_ACTIONS)[number];

export const DECLINE_REASONS = [
  "NOT_INTERESTED",
  "WRONG_PERSON",
  "ALREADY_RESOLVED",
  "NOT_NOW",
  "MESSAGE_INSTEAD",
  "OTHER",
] as const;
export type DeclineReason = (typeof DECLINE_REASONS)[number];

export const OUTCOME_CODES = [
  "RESOLVED",
  "FOLLOW_UP_REQUIRED",
  "NO_DECISION",
  "CALL_BACK",
  "DOCUMENT_REQUIRED",
  "TASK_CREATED",
  "MEETING_REQUIRED",
  "ESCALATED",
  "CANCELLED",
] as const;
export type OutcomeCode = (typeof OUTCOME_CODES)[number];

export interface Transition {
  readonly from: IntentStatus;
  readonly to: IntentStatus;
  readonly actors: readonly Actor[];
}

export const TRANSITIONS: readonly Transition[] = [
  { from: "DRAFT", to: "REQUESTED", actors: ["CALLER"] },
  { from: "DRAFT", to: "CANCELLED", actors: ["CALLER"] },
  { from: "REQUESTED", to: "DELIVERED", actors: ["SYSTEM"] },
  { from: "REQUESTED", to: "EXPIRED", actors: ["SYSTEM"] },
  { from: "REQUESTED", to: "CANCELLED", actors: ["CALLER"] },
  { from: "DELIVERED", to: "ACCEPTED", actors: ["RECEIVER"] },
  { from: "DELIVERED", to: "RESCHEDULED", actors: ["RECEIVER"] },
  { from: "DELIVERED", to: "SCHEDULED", actors: ["RECEIVER"] },
  { from: "DELIVERED", to: "DECLINED", actors: ["RECEIVER"] },
  { from: "DELIVERED", to: "EXPIRED", actors: ["SYSTEM"] },
  { from: "DELIVERED", to: "CANCELLED", actors: ["CALLER"] },
  { from: "DELIVERED", to: "IN_PROGRESS", actors: ["SYSTEM"] }, // PSTN fallback only, ADR 0008
  { from: "ACCEPTED", to: "IN_PROGRESS", actors: ["SYSTEM"] },
  { from: "ACCEPTED", to: "EXPIRED", actors: ["SYSTEM"] },
  { from: "ACCEPTED", to: "CANCELLED", actors: ["CALLER"] },
  { from: "RESCHEDULED", to: "SCHEDULED", actors: ["CALLER"] },
  { from: "RESCHEDULED", to: "DECLINED", actors: ["RECEIVER"] },
  { from: "RESCHEDULED", to: "CANCELLED", actors: ["CALLER", "RECEIVER"] },
  { from: "RESCHEDULED", to: "EXPIRED", actors: ["SYSTEM"] },
  { from: "SCHEDULED", to: "IN_PROGRESS", actors: ["SYSTEM"] },
  { from: "SCHEDULED", to: "RESCHEDULED", actors: ["CALLER", "RECEIVER"] },
  { from: "SCHEDULED", to: "DECLINED", actors: ["RECEIVER"] },
  { from: "SCHEDULED", to: "CANCELLED", actors: ["CALLER", "RECEIVER"] },
  { from: "SCHEDULED", to: "EXPIRED", actors: ["SYSTEM"] },
  { from: "IN_PROGRESS", to: "COMPLETED", actors: ["CALLER", "RECEIVER", "SYSTEM"] },
  { from: "IN_PROGRESS", to: "FOLLOW_UP_REQUIRED", actors: ["CALLER", "RECEIVER", "SYSTEM"] },
  { from: "FOLLOW_UP_REQUIRED", to: "COMPLETED", actors: ["CALLER", "RECEIVER", "SYSTEM"] },
];

export const TERMINAL_STATUSES: ReadonlySet<IntentStatus> = new Set([
  "COMPLETED",
  "DECLINED",
  "CANCELLED",
  "EXPIRED",
]);

export const EXPIRABLE_STATUSES: ReadonlySet<IntentStatus> = new Set([
  "REQUESTED",
  "DELIVERED",
  "ACCEPTED",
  "RESCHEDULED",
  "SCHEDULED",
]);

export function canTransition(from: IntentStatus, to: IntentStatus, actor: Actor): boolean {
  return TRANSITIONS.some((t) => t.from === from && t.to === to && t.actors.includes(actor));
}

export function isTerminal(status: IntentStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

const ACTION_TARGET: Readonly<Record<ResponseAction, IntentStatus>> = {
  ACCEPT: "ACCEPTED",
  LATER: "SCHEDULED",
  PROPOSE: "RESCHEDULED",
  SCHEDULE: "SCHEDULED",
  MESSAGE: "DECLINED",
  DECLINE: "DECLINED",
};

/**
 * Receiver actions to offer on incoming intent screen for a given status.
 * SCHEDULE is offered only when caller has offered slots.
 */
export function availableReceiverActions(
  status: IntentStatus,
  hasOfferedSlots: boolean,
): ResponseAction[] {
  return RESPONSE_ACTIONS.filter((action) => {
    if (action === "SCHEDULE" && !hasOfferedSlots) return false;
    if (status === "SCHEDULED") return action === "PROPOSE" || action === "DECLINE";
    if (status !== "DELIVERED") return false;
    return canTransition(status, ACTION_TARGET[action], "RECEIVER");
  });
}

const PRIORITY_RANK: Readonly<Record<Priority, number>> = { LOW: 0, NORMAL: 1, IMPORTANT: 2, URGENT: 3 };

export function priorityRank(p: Priority): number {
  return PRIORITY_RANK[p];
}

/**
 * Verified styling (badge, organisation name) is allowed only for organisation level verification.
 * UI must call this rather than inspect fields itself, so unverified senders can never look verified.
 */
export function showsVerifiedBadge(level: VerificationLevel): boolean {
  return level === "ORG" || level === "ORG_AGENT_NUMBER";
}
