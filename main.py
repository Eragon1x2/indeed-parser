from config import settings
from scraperkit.utils.runner import Runner


def main() -> None:
    Runner(settings.runner.package_name).run_once(
        settings.runner.spider_name,
    )


if __name__ == "__main__":
    main()
