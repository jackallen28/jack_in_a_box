"""Entry point:  python -m app"""
from __future__ import annotations

import logging
import socket
import sys

from . import plugins
from .backup import BackupService
from .birthdays import BirthdayService
from .calsync import CalendarSync
from .config import ConfigStore
from .gpio import ButtonController
from .mailbox import MailPoller
from .power import FanController, PowerManager
from .service import RingHandler, Service
from .sheet import SheetPoller
from .web import create_app


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    logging.getLogger().addHandler(RingHandler())
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("waitress").setLevel(logging.WARNING)

    cfg = ConfigStore()
    svc = Service(cfg)
    from . import aifeedback, llm
    llm.USER_EXAMPLES_FILE = svc.data_dir / "ai_examples.json"      # your reviewed summaries (AI review tab) become worked examples
    aifeedback.write_examples(svc.db, llm.USER_EXAMPLES_FILE, cfg.data)
    plugins.load(cfg.data["integrations"].get("plugins_enabled", True))
    poller, cal = MailPoller(cfg, svc), CalendarSync(cfg, svc)
    svc.start()
    poller.start()
    cal.start()
    button = ButtonController(cfg, svc)
    button.start()
    fan, power = FanController(cfg), PowerManager(cfg, svc)
    fan.start()
    power.start()
    sheet = SheetPoller(cfg, svc)
    sheet.start()
    from .airprint import AirPrintService
    airprint = AirPrintService(cfg, svc)
    airprint.start()
    birthdays, backup = BirthdayService(cfg, svc), BackupService(cfg, svc)
    birthdays.start()
    backup.start()
    app = create_app(cfg, svc, poller, cal, button, fan, power, sheet, airprint, birthdays, backup)

    port = int(cfg.data["web"].get("port", 8080))
    log = logging.getLogger("app")
    log.info("Config page: http://%s:%d  (or http://%s.local:%d)", lan_ip(), port, socket.gethostname(), port)
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=4)
    except ImportError:
        app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
