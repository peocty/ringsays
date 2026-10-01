"""Background worker: delivery ladder, expiry sweep, outbox relay, webhook dispatch.

Run: python -m app.worker. Uses worker database role. Several workers may run at once: delivery, expiry
and webhooks lock per row; outbox relay runs on one worker at a time by advisory lock.
Local environment wires MOCK push, directory and HTTP senders; production must configure real ones.
"""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable
from datetime import UTC, datetime

from app.core.config import settings
from app.core.db import worker_tx
from app.modules.client.directory import DbRecipientDirectory
from app.modules.delivery import adapters
from app.modules.delivery import service as delivery
from app.modules.intent import jobs
from app.modules.webhooks import service as webhooks
from app.platform import outbox

log = logging.getLogger("ringsays.worker")
TICK_S = 2.0
_running = True


def _stop(*_: object) -> None:
    global _running
    _running = False


def tick(now: datetime, publisher: outbox.Publisher, sender: webhooks.HttpSender) -> dict[str, int]:
    """One pass of every job. Each job is isolated: one failing never stops the others."""
    result = {"delivered": 0, "held": 0, "expired": 0, "relayed": 0, "webhooks": 0, "errors": 0}

    def run(name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:
            result["errors"] += 1
            log.error("job %s failed: %s", name, type(exc).__name__)

    def do_delivery() -> None:
        stats = delivery.deliver_due(now, adapters.get_directory(), adapters.get_push_sender())
        result["delivered"], result["held"] = stats.delivered, stats.held
        result["errors"] += stats.errors

    def do_expiry() -> None:
        result["expired"] = jobs.expire_due(now)

    def do_relay() -> None:
        with worker_tx() as conn:
            result["relayed"] = outbox.relay_once(conn, publisher, now)

    def do_webhooks() -> None:
        result["webhooks"] = webhooks.dispatch_due(now, sender)

    for name, fn in (
        ("delivery", do_delivery),
        ("expiry", do_expiry),
        ("relay", do_relay),
        ("webhooks", do_webhooks),
    ):
        run(name, fn)
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings.assert_safe_for_environment()
    adapters.configure(directory=DbRecipientDirectory())
    if settings.use_mock_adapters:
        log.warning("worker running with MOCK push, broker and webhook sender")
        publisher: outbox.Publisher = outbox.MockPublisher()
        sender: webhooks.HttpSender = webhooks.MockHttpSender()
    else:
        publisher = outbox.NatsPublisher(settings.nats_url)
        sender = webhooks.HttpxSender()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while _running:
        started = time.monotonic()
        try:
            result = tick(datetime.now(UTC), publisher, sender)
            if any(result.values()):
                log.info("tick %s", result)
        except Exception as exc:  # keep worker alive; next tick retries
            log.error("tick failed: %s", type(exc).__name__)
        time.sleep(max(0.0, TICK_S - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
