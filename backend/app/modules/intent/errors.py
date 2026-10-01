"""Domain errors. API layer maps each to an RFC 9457 problem response with a stable code."""

from __future__ import annotations


class IntentError(Exception):
    code: str = "intent_error"
    http_status: int = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class InvalidTransition(IntentError):
    code = "invalid_transition"
    http_status = 409


class ActorNotAllowed(IntentError):
    code = "actor_not_allowed"
    http_status = 409


class IntentExpired(IntentError):
    code = "intent_expired"
    http_status = 410


class RuleViolation(IntentError):
    """Request is well formed but breaks a business rule, for example priority above purpose code cap."""

    code = "rule_violation"
    http_status = 422
