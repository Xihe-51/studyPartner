"""本地 inline 周期调度器的门控与启停。

背景：本地只起 uvicorn 时没有 celery beat，预约发布和到点收卷本来永不执行，
而且不报错——学生关掉浏览器后作答永久停在 in_progress，教师既批不了也看不到。

这里锁住两件事：
1. 兜底调度器按「发布 60s / 收卷 300s」的节奏运行，启动后立刻先跑一轮；
2. 它**不**拿 worker 探活当判据。ping 只证明有 worker，证明不了 beat 存在，
   旧实现因此会在「有 worker、没 beat」的部署里继续静默不调度。
"""
import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

from app.core import task_dispatch as td


class _StopLoop(BaseException):
    """让被测循环干净退出。用 BaseException 是为了不被循环里的 except Exception 吞掉。"""


class _AsyncShim:
    """只替换 td.asyncio 这个名字，不动全局 asyncio 模块。"""

    def __init__(self, real, sleep_impl):
        self._real = real
        self._sleep = sleep_impl

    def sleep(self, seconds):
        return self._sleep(seconds)

    def __getattr__(self, name):
        return getattr(self._real, name)


class InlineSchedulerLoopTests(unittest.IsolatedAsyncioTestCase):
    async def _drive(self, ticks, extra_patches=()):
        """跑被测循环固定 tick 数，返回 (调用记录, clock 终值)。

        时钟从 10000 起——真机上 time.monotonic() 就是个大数，从 0 起会掩盖
        「启动后立刻跑一轮」这个行为。
        """
        clock = SimpleNamespace(t=10000.0)
        calls = []
        state = {"n": 0}
        real_sleep = asyncio.sleep

        async def fake_publish():
            calls.append(("publish", clock.t))
            return 0

        async def fake_finalize():
            calls.append(("finalize", clock.t))
            return 0

        async def fake_sleep(seconds):
            state["n"] += 1
            if state["n"] > ticks:
                raise _StopLoop()
            clock.t += seconds
            await real_sleep(0)

        shim = _AsyncShim(asyncio, fake_sleep)
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(td, "time", SimpleNamespace(monotonic=lambda: clock.t))
            )
            stack.enter_context(mock.patch.object(td, "asyncio", shim))
            stack.enter_context(mock.patch.object(td, "_run_publish_due_once", fake_publish))
            stack.enter_context(mock.patch.object(td, "_run_finalize_expired_once", fake_finalize))
            for target, replacement in extra_patches:
                stack.enter_context(mock.patch.object(td, target, replacement))
            with self.assertRaises(_StopLoop):
                await td._periodic_assessment_loop()
        return calls, clock.t

    async def test_first_round_runs_immediately_on_startup(self):
        """重启后积压的到期卷子要马上处理，不能等第一个间隔过去。"""
        calls, _ = await self._drive(ticks=0)
        self.assertEqual(calls[0], ("publish", 10000.0))
        self.assertIn(("finalize", 10000.0), calls)

    async def test_publish_and_finalize_keep_their_own_cadence(self):
        calls, _ = await self._drive(ticks=10)

        publish_at = [t for kind, t in calls if kind == "publish"]
        finalize_at = [t for kind, t in calls if kind == "finalize"]

        self.assertGreaterEqual(len(publish_at), 2)
        self.assertGreaterEqual(len(finalize_at), 2)

        # 发布每分钟一次
        for earlier, later in zip(publish_at, publish_at[1:]):
            self.assertGreaterEqual(later - earlier, td._PUBLISH_INTERVAL_SECONDS)
        # 收卷每 5 分钟一次，必须比发布稀
        for earlier, later in zip(finalize_at, finalize_at[1:]):
            self.assertGreaterEqual(later - earlier, td._FINALIZE_INTERVAL_SECONDS)
        self.assertLess(len(finalize_at), len(publish_at))

    async def test_worker_probe_does_not_gate_the_loop(self):
        """回归：旧实现用 workers_alive() 判断「beat 在线」，有 worker 没 beat 时会静默停摆。"""

        def boom():
            raise AssertionError("周期兜底不应依赖 worker 探活")

        calls, _ = await self._drive(ticks=0, extra_patches=[("workers_alive", boom)])
        self.assertTrue(any(kind == "publish" for kind, _ in calls))

    async def test_one_failure_does_not_kill_the_loop(self):
        """单轮报错（比如数据库抖动）不能让调度器整个停掉。"""
        clock = SimpleNamespace(t=10000.0)
        state = {"n": 0, "calls": 0}
        real_sleep = asyncio.sleep

        async def flaky():
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("db hiccup")
            return 0

        async def fake_sleep(seconds):
            state["n"] += 1
            if state["n"] > 4:
                raise _StopLoop()
            clock.t += seconds
            await real_sleep(0)

        with mock.patch.object(td, "time", SimpleNamespace(monotonic=lambda: clock.t)), \
             mock.patch.object(td, "asyncio", _AsyncShim(asyncio, fake_sleep)), \
             mock.patch.object(td, "_run_publish_due_once", flaky), \
             mock.patch.object(td, "_run_finalize_expired_once", flaky):
            with self.assertRaises(_StopLoop):
                await td._periodic_assessment_loop()

        # 第一轮抛错后仍继续跑到被 fake_sleep 打断
        self.assertGreater(state["calls"], 1)


class InlineSchedulerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        td._periodic_task = None

    async def test_start_is_idempotent_and_stop_cancels(self):
        async def noop():
            return 0

        with mock.patch.object(td, "_run_publish_due_once", noop), \
             mock.patch.object(td, "_run_finalize_expired_once", noop):
            td.start_assessment_scheduler()
            first = td._periodic_task
            self.assertIsNotNone(first)

            td.start_assessment_scheduler()
            self.assertIs(td._periodic_task, first, "重复 start 不应再起一个调度器")

            await asyncio.sleep(0.01)
            await td.stop_assessment_scheduler()
            self.assertIsNone(td._periodic_task)
            self.assertTrue(first.cancelled() or first.done())

    async def test_stop_without_start_is_safe(self):
        await td.stop_assessment_scheduler()
        self.assertIsNone(td._periodic_task)


if __name__ == "__main__":
    unittest.main()
