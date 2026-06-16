import json

import scrapy

from config import settings
from crawler.utils.query_manager import QueryManager
from crawler.utils.parser import build_job_item


class IndeedBasicSpider(scrapy.Spider):
    name = "indeed_basic"
    query = "python"
    location = "pl"

    async def start(self):
        search_query = self.__dict__.get("query") or settings.scraper.query or self.query
        queries = [q.strip() for q in search_query.split(",") if q.strip()]
        for q in queries:
            yield self._make_search_request(query=q, page=1)

    def _make_search_request(
        self, query: str, page: int, cursor: str | None = None
    ) -> scrapy.Request:
        loc = self.__dict__.get("location") or settings.scraper.location or self.location

        variables = {
            "what": query,
            "cursor": cursor,
        }
        if loc:
            variables["location"] = {
                "where": loc,
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
        data = json.loads(response.text)
        job_search = data["data"]["jobSearch"]
        results = job_search["results"]

        if not results:
            self.logger.warning(f"No more jobs found. Response text: {response.text}")
            return

        for res_item in results:
            job = res_item["job"]
            jk = job["key"]
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
                    "title": job["title"],
                    "company": job["employer"]["name"] if job["employer"] else None,
                    "location": job["location"]["formatted"]["long"],
                    "search_data": res_item,
                    "requires_auth": True,
                },
                dont_filter=True,
            )

        query = response.meta["query"]
        page = response.meta["page"]
        next_cursor = job_search["pageInfo"]["nextCursor"]
        if next_cursor:
            self.logger.info(f"Navigating query '{query}' to page {page + 1}...")
            yield self._make_search_request(
                query=query, cursor=next_cursor, page=page + 1
            )

    def get_job(self, response):
        meta = response.meta
        details_raw = json.loads(response.text)
        yield build_job_item(meta, details_raw)
