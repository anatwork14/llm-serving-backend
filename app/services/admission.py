import asyncio
import time
from dataclasses import dataclass

from app.config import get_settings


class AdmissionQueueFull(RuntimeError):
    pass


class AdmissionTimeout(RuntimeError):
    pass


@dataclass(slots=True)
class AdmissionSnapshot:
    active_total: int
    active_background: int
    waiting_foreground: int
    waiting_background: int
    max_concurrent: int
    max_background: int
    max_queue: int

    def as_dict(self) -> dict[str, int]:
        return {
            "active_total": self.active_total,
            "active_background": self.active_background,
            "waiting_foreground": self.waiting_foreground,
            "waiting_background": self.waiting_background,
            "max_concurrent": self.max_concurrent,
            "max_background": self.max_background,
            "max_queue": self.max_queue,
        }


class AdmissionLease:
    def __init__(
        self,
        controller: "LlmAdmissionController",
        *,
        background: bool,
        waited_ms: float,
    ) -> None:
        self._controller = controller
        self.background = background
        self.waited_ms = waited_ms
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._controller._release(background=self.background)


class LlmAdmissionController:
    """Bounded, foreground-priority admission for expensive LLM requests.

    llama.cpp already supports continuous batching and deferred requests. This
    controller deliberately sits in front of it so UI background jobs cannot
    fill every model slot or create an unbounded upstream queue.
    """

    def __init__(
        self,
        *,
        max_concurrent: int,
        max_background: int,
        max_queue: int,
        queue_timeout_seconds: float,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        if max_background < 0 or max_background > max_concurrent:
            raise ValueError("max_background must be between 0 and max_concurrent")
        if max_queue < 0:
            raise ValueError("max_queue must be >= 0")
        if queue_timeout_seconds <= 0:
            raise ValueError("queue_timeout_seconds must be > 0")

        self.max_concurrent = max_concurrent
        self.max_background = max_background
        self.max_queue = max_queue
        self.queue_timeout_seconds = queue_timeout_seconds

        self._condition = asyncio.Condition()
        self._active_total = 0
        self._active_background = 0
        self._waiting_foreground = 0
        self._waiting_background = 0

    def _can_enter(self, *, background: bool) -> bool:
        if self._active_total >= self.max_concurrent:
            return False
        if not background:
            return True
        if self.max_background == 0:
            return False
        if self._active_background >= self.max_background:
            return False
        # Do not start housekeeping while a real chat is waiting.
        return self._waiting_foreground == 0

    async def acquire(self, *, background: bool) -> AdmissionLease:
        started = time.perf_counter()

        async with self._condition:
            if self._can_enter(background=background):
                self._active_total += 1
                if background:
                    self._active_background += 1
                return AdmissionLease(
                    self,
                    background=background,
                    waited_ms=(time.perf_counter() - started) * 1000,
                )

            waiting_total = self._waiting_foreground + self._waiting_background
            if waiting_total >= self.max_queue:
                raise AdmissionQueueFull("LLM request queue is full")

            if background:
                self._waiting_background += 1
            else:
                self._waiting_foreground += 1

            deadline = asyncio.get_running_loop().time() + self.queue_timeout_seconds
            try:
                while not self._can_enter(background=background):
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise AdmissionTimeout("Timed out waiting for an LLM slot")
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                    except asyncio.TimeoutError as exc:
                        raise AdmissionTimeout(
                            "Timed out waiting for an LLM slot"
                        ) from exc

                self._active_total += 1
                if background:
                    self._active_background += 1
            finally:
                if background:
                    self._waiting_background -= 1
                else:
                    self._waiting_foreground -= 1

            return AdmissionLease(
                self,
                background=background,
                waited_ms=(time.perf_counter() - started) * 1000,
            )

    async def _release(self, *, background: bool) -> None:
        async with self._condition:
            self._active_total = max(0, self._active_total - 1)
            if background:
                self._active_background = max(0, self._active_background - 1)
            self._condition.notify_all()

    def snapshot(self) -> AdmissionSnapshot:
        return AdmissionSnapshot(
            active_total=self._active_total,
            active_background=self._active_background,
            waiting_foreground=self._waiting_foreground,
            waiting_background=self._waiting_background,
            max_concurrent=self.max_concurrent,
            max_background=self.max_background,
            max_queue=self.max_queue,
        )


settings = get_settings()
llm_admission = LlmAdmissionController(
    max_concurrent=settings.llm_max_concurrent_requests,
    max_background=settings.llm_max_background_requests,
    max_queue=settings.llm_max_queue_size,
    queue_timeout_seconds=settings.llm_queue_timeout_seconds,
)
