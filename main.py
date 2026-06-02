import argparse

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from indeed_scraper.spiders.indeed_basic import IndeedBasicSpider


def main() -> None:
    parser = argparse.ArgumentParser(description="Indeed job scraper")
    parser.add_argument("--query", default="python", help="Job search query")
    parser.add_argument("--location", default="Warszawa", help="Job location")
    parser.add_argument("--limit", default="all", help="Max jobs to scrape (number or 'all')")
    parser.add_argument("--force-login", action="store_true", help="Force new login even if session exists")
    args = parser.parse_args()

    settings = get_project_settings()
    process = CrawlerProcess(settings)
    process.crawl(
        IndeedBasicSpider,
        query=args.query,
        location=args.location,
        limit=args.limit,
        force_login=args.force_login,
    )
    process.start()


if __name__ == "__main__":
    main()
