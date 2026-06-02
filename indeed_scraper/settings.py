import os

from dotenv import load_dotenv

load_dotenv()

BOT_NAME = "indeed_scraper"

INDEED_API_KEY = os.environ.get("INDEED_API_KEY", "")
INDEED_BDT = os.environ.get("INDEED_BDT", "")
CAPTCHA_API_KEY = os.environ.get("CAPTCHA_API_KEY", "")

SPIDER_MODULES = ["indeed_scraper.spiders"]
NEWSPIDER_MODULE = "indeed_scraper.spiders"

ROBOTSTXT_OBEY = False

CONCURRENT_REQUESTS = 1
CONCURRENT_REQUESTS_PER_DOMAIN = 1

DOWNLOAD_DELAY = 5
RANDOMIZE_DOWNLOAD_DELAY = True

COOKIES_ENABLED = True

DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

DOWNLOADER_MIDDLEWARES = {
    "indeed_scraper.middlewares.IndeedSessionMiddleware": 543,
}

ITEM_PIPELINES = {
    "indeed_scraper.pipelines.DuplicatesPipeline": 300,
}

FEEDS = {
    "data/%(name)s_%(time)s.json": {
        "format": "json",
        "encoding": "utf8",
        "indent": 2,
    }
}
