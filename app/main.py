import time

from app.config import AppConfig
from app.dispatcher import Dispatcher
from app.gateway.feishu import FeishuGateway
from app.scheduler.loop import SchedulerLoop
from app.utils.logging import configure_logging


def main() -> None:
    config = AppConfig.load()
    configure_logging(config.log_level)

    dispatcher = Dispatcher(config=config)
    gateway = FeishuGateway(config=config, dispatcher=dispatcher)
    scheduler = SchedulerLoop(config=config, dispatcher=dispatcher, deliver_message=gateway.deliver)

    scheduler.start()
    gateway.start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
