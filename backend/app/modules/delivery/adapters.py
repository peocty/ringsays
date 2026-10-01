"""Delivery adapter interfaces and MOCK implementations.

Real adapters (APNs, FCM, identity directory from stage 4) implement the same Protocols. Mocks record
what they would send and never claim delivery to a real device.

Push payloads carry an intent id and a category only: never the reason text, names or references.
Push content passes through Apple and Google, so the app fetches details over RingSays API after wake.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from app.modules.preference.engine import Preferences


@dataclass(frozen=True, slots=True)
class PushTarget:
    device_id: UUID
    platform: str  # IOS or ANDROID
    token: str


@dataclass(frozen=True, slots=True)
class Recipient:
    """A RingSays user reachable at a phone number, as resolved by identity module."""

    user_ref: UUID
    devices: tuple[PushTarget, ...]
    preferences: Preferences = field(default_factory=Preferences)


class RecipientDirectory(Protocol):
    def lookup(self, phone_e164: str) -> Recipient | None: ...


@dataclass(frozen=True, slots=True)
class PushResult:
    ok: bool
    error: str | None = None


class PushSender(Protocol):
    def send(self, target: PushTarget, kind: str, intent_id: UUID) -> PushResult: ...


@dataclass
class MockRecipientDirectory:
    """MOCK: phone to recipient map configured by tests or local seed."""

    by_phone: dict[str, Recipient] = field(default_factory=dict)

    def lookup(self, phone_e164: str) -> Recipient | None:
        return self.by_phone.get(phone_e164)


@dataclass
class MockPushSender:
    """MOCK: records pushes. `fail_devices` simulates provider rejection (for example expired token)."""

    sent: list[tuple[UUID, str, UUID]] = field(default_factory=list)
    fail_devices: set[UUID] = field(default_factory=set)

    def send(self, target: PushTarget, kind: str, intent_id: UUID) -> PushResult:
        if target.device_id in self.fail_devices:
            return PushResult(ok=False, error="MockPushSender: device token rejected")
        self.sent.append((target.device_id, kind, intent_id))
        return PushResult(ok=True)


_directory: RecipientDirectory = MockRecipientDirectory()
_push: PushSender = MockPushSender()


def get_directory() -> RecipientDirectory:
    return _directory


def get_push_sender() -> PushSender:
    return _push


def configure(directory: RecipientDirectory | None = None, push: PushSender | None = None) -> None:
    """Install adapters at startup (or in tests). Production wiring replaces both mocks."""
    global _directory, _push
    if directory is not None:
        _directory = directory
    if push is not None:
        _push = push
