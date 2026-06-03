import logging
from multiprocessing import Process
from typing import Any

from scraperkit.utils.loader import load_spiders

logger = logging.getLogger(__name__)


class ScrapyProcessLauncher:
    def __init__(self, package_name: str) -> None:
        self.package_name = package_name

    def start(self, spider_name: str, spider_kwargs: dict[str, Any] | None = None) -> None:
        process = Process(
            target=self._run,
            args=(self.package_name, spider_name, spider_kwargs or {}),
            daemon=False,
        )
        process.start()
        logger.info(f"Spider process started: {spider_name} (pid={process.pid})")
        process.join()
        logger.info(f"Spider process finished: {spider_name} (exitcode={process.exitcode})")

    @staticmethod
    def _run(package_name: str, spider_name: str, spider_kwargs: dict[str, Any]) -> None:
        from scrapy.crawler import CrawlerProcess
        from scrapy.utils.project import get_project_settings

        spiders = load_spiders(package_name)
        if spider_name not in spiders:
            raise RuntimeError(
                f"Unknown spider: {spider_name}. Available: {list(spiders.keys())}"
            )

        process = CrawlerProcess(get_project_settings())
        process.crawl(spiders[spider_name], **spider_kwargs)
        process.start()


class Runner:
    def __init__(self, package_name: str) -> None:
        self.launcher = ScrapyProcessLauncher(package_name)

    def run_once(self, spider_name: str) -> None:
        self.launcher.start(spider_name)
