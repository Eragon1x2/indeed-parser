import json
import scrapy

from config import settings
from crawler.utils.query_manager import QueryManager


class IndeedBasicSpider(scrapy.Spider):
    name = "indeed_basic"

    def __init__(self, query: str | None = None, location: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.query = query or settings.scraper.query
        self.location = location or settings.scraper.location

    async def start(self):
        # Support multiple comma-separated keywords
        queries = [q.strip() for q in self.query.split(",") if q.strip()]
        for q in queries:
            yield self._make_search_request(query=q, cursor=None, page=1)

    def _make_search_request(
        self, query: str, cursor: str | None, page: int
    ) -> scrapy.Request:
        variables = {
            "what": query,
            "cursor": cursor,
        }
        if self.location:
            variables["location"] = {
                "where": self.location,
                "radius": 50,
                "radiusUnit": "MILES",
            }

        payload = {
            "query": QueryManager.load_query("searchjobs.graphql"),
            "variables": variables,
        }

        return scrapy.Request(
            url="https://apis.indeed.com/graphql",
            method="POST",
            body=json.dumps(payload),
            callback=self.get_search,
            meta={
                "query": query,
                "page": page,
                "cursor": cursor,
                "requires_auth": True,
            },
            dont_filter=True,
        )

    def get_search(self, response):
        try:
            data = json.loads(response.text)
        except Exception as e:
            self.logger.error(f"Failed to parse search response JSON: {e}")
            return

        job_search = data.get("data", {}).get("jobSearch", {})
        results = job_search.get("results", [])

        if not results:
            self.logger.warning(f"No more jobs found. Response text: {response.text}")
            return

        for res_item in results:
            job = res_item.get("job") or {}
            jk = job.get("key")
            if not jk:
                continue

            payload = {
                "query": QueryManager.load_query("viewjob.graphql"),
                "variables": {
                    "input": jk,
                    "enableEmployerInsights": False,
                    "jobResultTrackingKey": None,
                    "detailsInput": {
                        "viewjobCookieModel": {
                            "jobseekerCookieModel": {},
                            "clickTrackingLog": f"jk={jk}&previousPageNumber=1,last",
                            "previousPageNumber": "1,last",
                        },
                        "viewjobUrl": f"https://www.indeed.com/m/viewjob?jk={jk}",
                        "shouldQueryApplyRateLimit": True,
                    },
                    "isLoggedIn": False,
                },
            }

            self.logger.info(f"Scraping Job Key: {jk}")

            yield scrapy.Request(
                url="https://apis.indeed.com/graphql",
                method="POST",
                body=json.dumps(payload),
                callback=self.get_job,
                meta={
                    "job_key": jk,
                    "title": job.get("title"),
                    "company": job.get("sourceEmployerName")
                    or (job.get("employer") or {}).get("parentEmployer", {}).get("name"),
                    "loc": (job.get("location") or {}).get("formatted", {}).get("long"),
                    "search_data": res_item,
                    "requires_auth": True,
                },
                dont_filter=True,
            )

        query = response.meta["query"]
        page = response.meta["page"]
        next_cursor = job_search.get("pageInfo", {}).get("nextCursor")
        if next_cursor:
            self.logger.info(f"Navigating query '{query}' to page {page + 1}...")
            yield self._make_search_request(
                query=query, cursor=next_cursor, page=page + 1
            )

    def get_job(self, response):
        meta = response.meta
        try:
            details_raw = json.loads(response.text)
        except Exception as e:
            self.logger.error(
                f"Failed to parse job details response for {meta['job_key']}: {e}"
            )
            details_raw = {}

        yield {
            "job_key": meta["job_key"],
            "title": meta["title"],
            "company": meta["company"],
            "location": meta["loc"],
            "search_data": meta["search_data"],
            "details_raw": details_raw,
        }
