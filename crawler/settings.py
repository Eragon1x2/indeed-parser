BOT_NAME = "crawler"
SPIDER_MODULES = ["crawler.spiders"]
NEWSPIDER_MODULE = "crawler.spiders"

ROBOTSTXT_OBEY = False
CONCURRENT_REQUESTS = 1
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY = 5
RANDOMIZE_DOWNLOAD_DELAY = True
COOKIES_ENABLED = True

DOWNLOADER_MIDDLEWARES = {
    "crawler.middlewares.session.IndeedSessionMiddleware": 543,
}

ITEM_PIPELINES = {
    "crawler.pipelines.DuplicatesPipeline": 300,
}

FEEDS = {
    "data/%(name)s_%(time)s.json": {
        "format": "json",
        "encoding": "utf8",
        "indent": 2,
    }
}
