"""长耗时任务的派发兜底。

生产环境由 docker-compose 里的 sp-worker 消费队列；本地开发经常只起 uvicorn 不起
worker，任务会被静默投进 Redis 再无人消费 —— 表现就是拆题永远停在 pending，
日志里一条错误都没有。所以投递前先探一次 worker 存活，没有 worker 就直接在 API
进程里跑后台任务，保证功能可用、失败可查。
"""

import asyncio
import logging
import time
from typing import Set
from uuid import UUID

logger = logging.getLogger(__name__)

# worker 探活结果缓存，避免每建一份试卷都发一次广播
_PROBE_TTL_SECONDS = 5.0
_probe_state: dict = {"checked_at": 0.0, "alive": False}

# 持有强引用，否则 asyncio 会在任务完成前把它回收掉
_background_tasks: Set[asyncio.Task] = set()


def _probe_workers_sync(timeout: float = 0.5) -> bool:
    from app.core.celery_app import celery_app

    try:
        replies = celery_app.control.ping(timeout=timeout)
    except Exception as exc:  # broker 连不上、超时都算「没有 worker」
        logger.warning("Celery worker probe failed: %s", exc)
        return False
    return bool(replies)


async def workers_alive() -> bool:
    now = time.monotonic()
    if now - _probe_state["checked_at"] < _PROBE_TTL_SECONDS:
        return bool(_probe_state["alive"])

    # control.ping 是阻塞调用，扔到线程里免得卡住事件循环
    alive = await asyncio.to_thread(_probe_workers_sync)
    _probe_state["checked_at"] = now
    _probe_state["alive"] = alive
    if not alive:
        logger.warning(
            "No Celery worker responded; long-running tasks will run inline in the API process."
        )
    return alive


async def _run_inline(paper_id: UUID) -> None:
    from app.core.database import SessionLocal
    from app.services.assessment_service import AssessmentService

    try:
        async with SessionLocal() as db:
            await AssessmentService.run_parse(db, paper_id)
    except Exception as exc:
        logger.error("Inline assessment parse failed for %s: %s", paper_id, exc, exc_info=True)


def _track(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def dispatch_parse_task(paper_id: UUID) -> str:
    """投递拆题任务，返回实际走通的通道（"celery" 或 "inline"）。"""
    if await workers_alive():
        from app.tasks.assessment_tasks import parse_assessment_paper_task

        try:
            parse_assessment_paper_task.delay(str(paper_id))
            return "celery"
        except Exception as exc:
            logger.error(
                "Failed to enqueue assessment parse for %s: %s", paper_id, exc, exc_info=True
            )
            # broker 抖动时退化成进程内执行，别把任务丢进黑洞
            _probe_state["alive"] = False

    _track(asyncio.create_task(_run_inline(paper_id)))
    return "inline"


# ---------- 周期任务兜底 ----------
#
# 到点收卷与预约发布由 celery beat 驱动（见 celery_app.beat_schedule）。生产
# docker-compose 里 sp-beat 负责它，backend/worker 都把 ENABLE_INLINE_SCHEDULER
# 显式设为 false；本地开发只起 uvicorn 时 beat 根本不存在，于是：
#   - 学生关掉浏览器后，作答永久停在 in_progress，教师既批不了也看不到；
#   - 预约发布的试卷永远停在 awaiting_review，学生那边一直看不到。
# 两者都不会报错，只是「什么也没发生」，很难自己排查。
#
# 开关就用 ENABLE_INLINE_SCHEDULER —— 它已经表达「周期任务在本进程内跑」这个语义，
# 生产 compose 也据此关掉了。**不能拿 worker 探活当判据**：control.ping 只证明有
# worker，证明不了 beat 存在；「有 worker、没 beat」的部署会因此继续静默不调度，
# 而且比完全没有 Celery 更难发现。

_PUBLISH_INTERVAL_SECONDS = 60
_FINALIZE_INTERVAL_SECONDS = 300
_PERIODIC_TICK_SECONDS = 30


async def _run_publish_due_once() -> int:
    from app.core.database import SessionLocal
    from app.services.assessment_service import AssessmentService

    async with SessionLocal() as db:
        return await AssessmentService.publish_due_papers(db)


async def _run_finalize_expired_once() -> int:
    from app.core.database import SessionLocal
    from app.services.assessment_service import AssessmentService

    async with SessionLocal() as db:
        return await AssessmentService.finalize_expired_attempts(db)


async def _periodic_assessment_loop() -> None:
    # 首轮 last_* 为 0，monotonic() 是个大数，所以启动后立刻先跑一次，
    # 不必等到第一个间隔过去 —— 重启后积压的到期卷子要马上处理。
    last_publish = 0.0
    last_finalize = 0.0
    while True:
        try:
            now = time.monotonic()
            if now - last_publish >= _PUBLISH_INTERVAL_SECONDS:
                last_publish = now
                published = await _run_publish_due_once()
                if published:
                    logger.info("Inline scheduler published %d scheduled paper(s)", published)
            now = time.monotonic()
            if now - last_finalize >= _FINALIZE_INTERVAL_SECONDS:
                last_finalize = now
                finalized = await _run_finalize_expired_once()
                if finalized:
                    logger.info("Inline scheduler finalized %d expired attempt(s)", finalized)
        except asyncio.CancelledError:
            logger.info("Inline assessment scheduler cancelled.")
            break
        except Exception as exc:
            logger.error("Inline assessment scheduler error: %s", exc, exc_info=True)
        await asyncio.sleep(_PERIODIC_TICK_SECONDS)


_periodic_task: "asyncio.Task | None" = None


def start_assessment_scheduler() -> None:
    """在 API 进程内启动周期任务。仅在 ENABLE_INLINE_SCHEDULER 打开时调用。"""
    global _periodic_task
    if _periodic_task is None:
        _periodic_task = asyncio.get_running_loop().create_task(_periodic_assessment_loop())
        logger.info("Inline assessment scheduler started.")


async def stop_assessment_scheduler() -> None:
    global _periodic_task
    task = _periodic_task
    _periodic_task = None
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("Inline assessment scheduler stopped.")
