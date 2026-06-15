import json
from pathlib import Path

import scrapy

from config import settings


class IndeedBasicSpider(scrapy.Spider):
    name = "indeed_basic"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.query = kwargs.get("query", settings.scraper.query)
        self.location = kwargs.get("location", settings.scraper.location)
        self.limit = kwargs.get("limit", settings.scraper.limit)
        self.scraped_count = 0

        # Read GraphQL queries from file
        queries_dir = Path(__file__).parent.parent / "queries"
        with open(queries_dir / "searchjobs.graphql", encoding="utf-8") as f:
            self.search_query_tmpl = f.read().strip()
        with open(queries_dir / "viewjob.graphql", encoding="utf-8") as f:
            self.view_query = f.read().strip()

        # Parse limit
        limit_val = str(self.limit).lower()
        if limit_val == "all":
            self.max_results = float("inf")
        else:
            try:
                self.max_results = int(limit_val)
            except ValueError:
                self.max_results = 30

    async def start(self):
        # Support multiple comma-separated keywords
        queries = [q.strip() for q in self.query.split(",") if q.strip()]
        for q in queries:
            yield self._make_search_request(query=q, cursor=None, page=1)

    def _make_search_request(
        self, query: str, cursor: str | None, page: int
    ) -> scrapy.Request:
        what_clause = (
            f'what: "{query.replace("\\", "\\\\").replace('"', '\\"')}"' if query else ""
        )
        loc_clause = (
            f'location: {{where: "{self.location.replace("\\", "\\\\").replace('"', '\\"')}", radius: 50, radiusUnit: MILES}}'
            if self.location
            else ""
        )
        cursor_clause = f'cursor: "{cursor}"' if cursor else ""

        q_body = self.search_query_tmpl
        q_body = q_body.replace("{what}", what_clause)
        q_body = q_body.replace("{location}", loc_clause)
        q_body = q_body.replace("{cursor}", cursor_clause)
        q_body = q_body.replace("{filters}", "")

        return scrapy.Request(
            url="https://apis.indeed.com/graphql",
            method="POST",
            body=json.dumps({"query": q_body}),
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
            if self.scraped_count >= self.max_results:
                return

            job = res_item.get("job") or {}
            jk = job.get("key")
            if not jk:
                continue

            payload = {
                "query": self.view_query,
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

            self.scraped_count += 1
            self.logger.info(
                f"Scraping Job Key: {jk} ({self.scraped_count}/{self.max_results})"
            )

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
        if next_cursor and self.scraped_count < self.max_results:
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
