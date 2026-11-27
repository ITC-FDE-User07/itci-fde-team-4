"""Every failure raises an alert: a log line in the lab, plus an optional chat/email webhook in a real setup."""
import datetime as dt
import json
import logging
import os

from vastra_data import config

log = logging.getLogger("vastra_data")


def alert(message: str, **context) -> str:
    line = json.dumps({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                       "level": "ALERT", "message": message, **context}, default=str)
    os.makedirs(os.path.dirname(config.settings.alert_log), exist_ok=True)
    with open(config.settings.alert_log, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    log.warning("ALERT %s %s", message, json.dumps(context, default=str))
    if config.settings.alert_webhook:
        try:   # best effort: an unreachable webhook must not stop the load job
            import httpx
            httpx.post(config.settings.alert_webhook, json={"text": f"[vastra-data] {message}", **context}, timeout=2)
        except Exception as e:  # noqa: BLE001
            log.warning("alert webhook failed: %s", e)
    return line


def recent(n: int = 20) -> list[dict]:
    if not os.path.exists(config.settings.alert_log):
        return []
    with open(config.settings.alert_log, encoding="utf-8") as f:
        return [json.loads(x) for x in f.readlines()[-n:]]
