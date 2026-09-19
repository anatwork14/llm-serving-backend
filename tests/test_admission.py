import asyncio

import pytest

from app.services.admission import (
    AdmissionQueueFull,
    AdmissionTimeout,
    LlmAdmissionController,
)


@pytest.mark.asyncio
async def test_foreground_waiter_is_prioritized_over_background() -> None:
    controller = LlmAdmissionController(
        max_concurrent=1,
        max_background=1,
        max_queue=4,
        queue_timeout_seconds=1,
    )
    first = await controller.acquire(background=False)
    order: list[str] = []

    async def run(name: str, *, background: bool) -> None:
        lease = await controller.acquire(background=background)
        try:
            order.append(name)
            await asyncio.sleep(0)
        finally:
            await lease.release()

    background_task = asyncio.create_task(run("background", background=True))
    await asyncio.sleep(0)
    foreground_task = asyncio.create_task(run("foreground", background=False))
    await asyncio.sleep(0)

    await first.release()
    await asyncio.gather(foreground_task, background_task)

    assert order == ["foreground", "background"]


@pytest.mark.asyncio
async def test_queue_is_bounded() -> None:
    controller = LlmAdmissionController(
        max_concurrent=1,
        max_background=1,
        max_queue=1,
        queue_timeout_seconds=1,
    )
    first = await controller.acquire(background=False)
    queued = asyncio.create_task(controller.acquire(background=False))
    await asyncio.sleep(0)

    with pytest.raises(AdmissionQueueFull):
        await controller.acquire(background=False)

    await first.release()
    lease = await queued
    await lease.release()


@pytest.mark.asyncio
async def test_wait_timeout_does_not_leak_waiter_count() -> None:
    controller = LlmAdmissionController(
        max_concurrent=1,
        max_background=1,
        max_queue=2,
        queue_timeout_seconds=0.01,
    )
    first = await controller.acquire(background=False)

    with pytest.raises(AdmissionTimeout):
        await controller.acquire(background=True)

    snapshot = controller.snapshot()
    assert snapshot.waiting_background == 0
    assert snapshot.active_total == 1

    await first.release()
