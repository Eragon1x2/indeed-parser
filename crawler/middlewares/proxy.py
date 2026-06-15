from config import settings


class IndeedProxyMiddleware:
    def process_request(self, request, spider):
        if settings.scraper.proxy:
            request.meta["proxy"] = str(settings.scraper.proxy)
        return None
