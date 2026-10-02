from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings
from app.core.observability import configure_logging, init_sentry

configure_logging()
init_sentry("worker")

def _beat_schedule() -> dict:
    """Only enabled markets/features are scheduled (simple mode: MARKETS_ENABLED, FEATURES_DISABLED)."""
    from app.core.markets import enabled_markets

    s = get_settings()
    sched = {  # all times IST
        # NSE: after the 15:30 close plus vendor EOD publication delay (then NIFTY options)
        "eod-NSE": {"task": "app.workers.tasks.ingest_and_scan", "schedule": crontab(hour=18, minute=30, day_of_week="mon-fri"), "args": ("NSE",)},
        # Asia closes by ~13:30 IST (HK 16:00 HKT); Europe ~22:00 IST; US ~01:30–02:30 IST next day
        "eod-ASIA": {"task": "app.workers.tasks.ingest_and_scan", "schedule": crontab(hour=15, minute=0, day_of_week="mon-fri"), "args": ("ASIA",)},
        "eod-EUROPE": {"task": "app.workers.tasks.ingest_and_scan", "schedule": crontab(hour=23, minute=0, day_of_week="mon-fri"), "args": ("EUROPE",)},
        "eod-US": {"task": "app.workers.tasks.ingest_and_scan", "schedule": crontab(hour=3, minute=30, day_of_week="tue-sat"), "args": ("US",)},
        # FX daily bar rolls at 17:00 New York (~02:30 IST); crypto daily bar closes 00:00 UTC (05:30 IST), every day
        "eod-FX": {"task": "app.workers.tasks.ingest_and_scan", "schedule": crontab(hour=3, minute=0, day_of_week="tue-sat"), "args": ("FX",)},
        "eod-CRYPTO": {"task": "app.workers.tasks.ingest_and_scan", "schedule": crontab(hour=6, minute=0), "args": ("CRYPTO",)},
        # calendars daily before the Asian open; news refreshed hourly during the day
        "calendar-daily": {"task": "app.workers.tasks.calendar", "schedule": crontab(hour=5, minute=0)},
        # weekly retrain per market (new candidate only; activation stays a reviewed admin decision)
        **{f"ml-weekly-{m}": {"task": "app.workers.tasks.ml_train", "schedule": crontab(hour=4, minute=30, day_of_week="sun"), "args": (None, m)}
           for m in ("NSE", "CRYPTO", "US", "EUROPE", "ASIA", "FX")},
        "alerts-tick": {"task": "app.workers.tasks.alerts_tick", "schedule": crontab(minute="*/5")},
        "notifications-retry": {"task": "app.workers.tasks.retry_notifications", "schedule": crontab(minute="*/15")},
        "telegram-poll": {"task": "app.workers.tasks.telegram_poll", "schedule": crontab(minute="*")},
        "upstox-reminder": {"task": "app.workers.tasks.upstox_reminder", "schedule": crontab(hour=17, minute=45, day_of_week="mon-fri")},
        "retention-daily": {"task": "app.workers.tasks.retention_cleanup", "schedule": crontab(hour=3, minute=45)},
        "news-hourly": {"task": "app.workers.tasks.news_all", "schedule": crontab(minute=15, hour="7-23")},
    }
    on = set(enabled_markets())
    drop = {k for k, v in sched.items() if v["task"] in ("app.workers.tasks.ingest_and_scan", "app.workers.tasks.ml_train")
            and (v.get("args") or (None,))[-1] not in on}
    feature_tasks = {"news": "app.workers.tasks.news_all", "calendar": "app.workers.tasks.calendar", "ml": "app.workers.tasks.ml_train"}
    drop |= {k for k, v in sched.items() for f, t in feature_tasks.items() if v["task"] == t and not s.feature_on(f)}
    return {k: v for k, v in sched.items() if k not in drop}


s = get_settings()
celery = Celery("marketedge", broker=s.broker_url or "memory://", backend=None, include=["app.workers.tasks"])
celery.conf.update(
    task_always_eager=s.celery_always_eager,
    task_eager_propagates=False,
    task_acks_late=True,
    worker_hijack_root_logger=False,  # keep our (JSON) log format
    # Redis redelivers an un-acked message after `visibility_timeout`. With acks_late, any task running longer
    # than that (a full Upstox NSE ingest takes > 1 h) was handed out AGAIN after finishing — an endless loop that
    # blocked every scan. Must exceed the longest task (task_time_limit).
    broker_transport_options={"visibility_timeout": 6 * 3600},
    result_backend_transport_options={"visibility_timeout": 6 * 3600},
    task_reject_on_worker_lost=True,  # a killed worker's task is redelivered, not silently dropped
    broker_connection_retry_on_startup=True,
    worker_prefetch_multiplier=1,
    task_time_limit=3 * 3600,
    task_soft_time_limit=int(2.75 * 3600),
    # queues: heavy CPU work is isolated so notifications/alerts never wait behind a scan
    task_default_queue="default",
    task_routes={
        "app.workers.tasks.scan": {"queue": "scans"}, "app.workers.tasks.ingest": {"queue": "scans"},
        "app.workers.tasks.ingest_and_scan": {"queue": "scans"}, "app.workers.tasks.options": {"queue": "scans"},
        "app.workers.tasks.backtest": {"queue": "backtests"}, "app.workers.tasks.ml_train": {"queue": "backtests"},
        "app.workers.tasks.alerts_tick": {"queue": "notifications"}, "app.workers.tasks.retry_notifications": {"queue": "notifications"},
        "app.workers.tasks.telegram_poll": {"queue": "notifications"}, "app.workers.tasks.upstox_reminder": {"queue": "notifications"},
    },
    timezone="Asia/Kolkata",
    beat_schedule=_beat_schedule(),
)
