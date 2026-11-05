"""Wait for in-flight work to settle before propagating cancellation.

A cancelled asyncio.to_thread await does not stop its underlying thread. Demo
teardown must not report completion while a sender or persistence thread runs.
"""
import asyncio


async def settle_before_cancel(awaitable):
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done() and task.cancelled():
                raise
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return result


async def settled_thread(function, *args, **kwargs):
    return await settle_before_cancel(asyncio.to_thread(function, *args, **kwargs))
