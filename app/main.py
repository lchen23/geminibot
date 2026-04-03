import logging
import time

from app.config import AppConfig
from app.dispatcher import Dispatcher
from app.gateway.feishu import FeishuGateway
from app.scheduler.loop import SchedulerLoop
from app.utils.logging import configure_logging

logger = logging.getLogger(__name__)


def run_service() -> None:
    config = AppConfig.load()
    configure_logging(config.log_level)
    startup = config.run_startup_checks()
    for warning in startup.warnings:
        logger.warning("Startup check warning: %s", warning)

    dispatcher = Dispatcher(config=config)
    gateway = FeishuGateway(config=config, dispatcher=dispatcher)
    scheduler = SchedulerLoop(config=config, dispatcher=dispatcher, deliver_message=gateway.deliver)

    scheduler.start()
    gateway.start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Shutdown signal received.")
    finally:
        gateway.stop()
        scheduler.stop()


def main() -> None:
    run_service()


if __name__ == "__main__":
    main()
