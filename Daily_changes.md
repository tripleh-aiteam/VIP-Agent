/usr/local/lib/python3.12/site-packages/pydantic/_internal/_fields.py:160: UserWarning: Field "model_3d_uri" has conflict with protected namespace "model_".
You may be able to resolve this warning by setting `model_config['protected_namespaces'] = ()`.
  warnings.warn(
INFO:     Started server process [1]
INFO:     Waiting for application startup.
{"timestamp": "2026-04-14T05:41:09.505740", "level": "INFO", "service": "vip-orchestrator", "message": "event_bus: Redis unavailable (Error 111 connecting to localhost:6379. Connection refused.), using in-memory bus", "action": "event_bus.memory_fallback"}
{"timestamp": "2026-04-14T05:41:09.814501", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: loaded rule 'asset_summary_evening' cron='0 18 * * *'", "action": "scheduler.rule_loaded"}
==> Detected a new open port HTTP:8000
{"timestamp": "2026-04-14T05:41:09.883462", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: loaded rule 'realty_listing_daily' cron='0 10 * * *'", "action": "scheduler.rule_loaded"}
{"timestamp": "2026-04-14T05:41:09.952093", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: loaded rule 'weekly_summary_friday' cron='0 17 * * 5'", "action": "scheduler.rule_loaded"}
{"timestamp": "2026-04-14T05:41:10.020915", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: loaded rule 'asset_summary_morning' cron='*/2 * * * *'", "action": "scheduler.rule_loaded"}
{"timestamp": "2026-04-14T05:41:10.090072", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: loaded rule 'stock_analysis_morning' cron='0 9 * * *'", "action": "scheduler.rule_loaded"}
{"timestamp": "2026-04-14T05:41:10.158961", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: loaded rule 'stock_analysis_evening' cron='0 18 * * *'", "action": "scheduler.rule_loaded"}
{"timestamp": "2026-04-14T05:41:10.227844", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: loaded rule 'stock_analysis_weekly' cron='0 17 * * 5'", "action": "scheduler.rule_loaded"}
{"timestamp": "2026-04-14T05:41:10.296854", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: loaded rule 'realty_listing_morning' cron='0 9 * * *'", "action": "scheduler.rule_loaded"}
{"timestamp": "2026-04-14T05:41:10.365724", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: loaded rule 'realty_listing_evening' cron='0 18 * * *'", "action": "scheduler.rule_loaded"}
{"timestamp": "2026-04-14T05:41:10.434554", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: loaded rule 'realty_listing_weekly' cron='0 17 * * 5'", "action": "scheduler.rule_loaded"}
{"timestamp": "2026-04-14T05:41:10.434641", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: 10 rules loaded", "action": "scheduler.rules_loaded"}
{"timestamp": "2026-04-14T05:41:10.503768", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: started", "action": "scheduler.started"}
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:59456 - "HEAD / HTTP/1.1" 405 Method Not Allowed
INFO:     10.209.24.247:38570 - "GET /health HTTP/1.1" 200 OK
INFO:     10.209.24.247:38582 - "GET /health HTTP/1.1" 200 OK
==> Your service is live 🎉
==> 
==> ///////////////////////////////////////////////////////////
==> 
==> Available at your primary URL https://vip-orchestrator.onrender.com
==> 
==> ///////////////////////////////////////////////////////////
INFO:     10.209.24.247:38584 - "GET /health HTTP/1.1" 200 OK
INFO:     10.209.24.247:35956 - "GET /health HTTP/1.1" 200 OK
INFO:     10.209.24.247:35964 - "GET /health HTTP/1.1" 200 OK
INFO:     10.209.24.247:42042 - "GET /health HTTP/1.1" 200 OK
INFO:     10.209.24.247:42052 - "GET /health HTTP/1.1" 200 OK
INFO:     10.209.24.247:35656 - "GET /health HTTP/1.1" 200 OK
INFO:     10.209.24.247:35664 - "GET /health HTTP/1.1" 200 OK
INFO:     10.209.24.247:35680 - "GET /health HTTP/1.1" 200 OK
{"timestamp": "2026-04-14T05:42:00.000817", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: composing daily_summary report", "trace_id": "tr-sched-report-1776145320", "action": "scheduler.report"}
INFO:     10.209.24.247:40490 - "GET /health HTTP/1.1" 200 OK
{"timestamp": "2026-04-14T05:42:00.952048", "level": "INFO", "service": "vip-orchestrator", "message": "audit: report.daily_summary", "trace_id": "tr-sched-report-1776145320", "action": "report.daily_summary"}
{"timestamp": "2026-04-14T05:42:00.952244", "level": "INFO", "service": "vip-orchestrator", "message": "report composed: daily_summary (28 runs, 5 sections)", "trace_id": "tr-sched-report-1776145320", "action": "report.daily_summary"}
{"timestamp": "2026-04-14T05:42:01.226568", "level": "INFO", "service": "vip-orchestrator", "message": "scheduler: daily_summary report done", "action": "scheduler.report.done"}
INFO:     10.209.24.247:40504 - "GET /health HTTP/1.1" 200 OK
INFO:     10.209.24.247:55766 - "GET /health HTTP/1.1" 200 OK
INFO:     10.209.24.247:55774 - "GET /health HTTP/1.1" 200 OK
INFO:     10.209.24.247:45360 - "GET /health HTTP/1.1" 200 OK
INFO:     10.209.24.247:45364 - "GET /health HTTP/1.1" 200 OK