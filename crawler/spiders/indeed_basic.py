import asyncio
import random
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import scrapy

from config import settings
from crawler.utils.auth import perform_login_flow
from crawler.utils.graphql import IndeedGraphQLClient
from crawler.utils.temp_mail import TempMailManager

if TYPE_CHECKING:
    from playwright.async_api import Page


def _build_item(jk, title, company, loc, search_data, details_raw):
    item: dict = {
        "job_key": jk,
        "title": title,
        "company": company,
        "location": loc,
        "search_data": search_data,
    }
    if not details_raw:
        return item

    viewjob = (details_raw.get("data") or {}).get("viewjob") or {}
    detail_job = viewjob.get("job") or {}
    detail_info = viewjob.get("details") or {}
    comp = detail_job.get("compensation") or {}
    salary_range = (comp.get("baseSalary") or {}).get("range") or {}
    apply_method = detail_info.get("applyMethod") or {}

    item["description"] = (detail_job.get("description") or {}).get("text")
    item["date_published"] = detail_job.get("datePublished")
    item["salary"] = comp.get("formattedText")
    item["salary_min"] = salary_range.get("min")
    item["salary_max"] = salary_range.get("max")
    item["remote"] = (detail_info.get("remoteWorkModel") or {}).get("text")
    item["job_types"] = (detail_info.get("jobTypeAndShiftSchedule") or {}).get("jobTypes") or []
    item["benefits"] = [
        b.get("label")
        for b in (detail_info.get("benefit") or {}).get("benefits") or []
    ]
    item["apply_url"] = apply_method.get("applyUrl") or apply_method.get("continueUrl")
    item["address"] = (detail_info.get("location") or {}).get("formattedStreetAddress")
    return item


class IndeedBasicSpider(scrapy.Spider):
    name = "indeed_basic"

    custom_settings = {
        "LOG_LEVEL": "INFO",
        "CONCURRENT_REQUESTS": 1,
    }

    def __init__(self, **kwargs: str) -> None:
        super().__init__(**kwargs)
        self.query = settings.scraper.query
        self.location = settings.scraper.location
        self.limit = settings.scraper.limit
        self.force_login = settings.scraper.force_login
        proxy = str(settings.scraper.proxy) if settings.scraper.proxy else None
        self.graphql_client = IndeedGraphQLClient(proxy=proxy)
        self.mail_manager = TempMailManager()

    async def start(self) -> AsyncGenerator:
        yield scrapy.Request(
            url="https://pl.indeed.com/",
            callback=self.parse,
            meta={"requires_auth": True},
            dont_filter=True,
        )

    async def perform_login_flow(self, page: "Page", user_data_dir: str | None = None) -> dict:
        return await perform_login_flow(
            page, self.mail_manager, user_data_dir, settings.captcha_api_key
        )

    def _results_wanted(self) -> float:
        if str(self.limit).lower() == "all":
            return float("inf")
        try:
            return int(self.limit)
        except ValueError:
            return 30

    async def _yield_page_items(self, results: list, session_data: dict) -> AsyncGenerator:
        for res_item in results:
            job = res_item.get("job")
            if not job:
                continue
            jk = job.get("key")
            title = job.get("title")
            employer = job.get("employer") or {}
            company = job.get("sourceEmployerName") or employer.get("parentEmployer", {}).get("name")
            loc = (job.get("location") or {}).get("formatted", {}).get("long")
            self.logger.info(f"[JOB] {jk} | {title} | {company} | {loc}")
            details_raw = await self.graphql_client.fetch_job(jk, session_data)
            yield _build_item(jk, title, company, loc, res_item, details_raw)
            await asyncio.sleep(random.uniform(1.0, 2.0))  # noqa: S311

    async def _scrape_jobs(self, session_data: dict) -> AsyncGenerator:
        cursor = None
        page_num = 1
        scraped = 0
        limit = self._results_wanted()

        while scraped < limit:
            self.logger.info(f"Page {page_num} (scraped: {scraped})")
            result = await self.graphql_client.search_jobs(
                query=self.query, location=self.location,
                cursor=cursor, session_data=session_data,
            )
            if not result:
                break

            job_search = result.get("data", {}).get("jobSearch", {})
            results = job_search.get("results", [])
            if not results:
                break

            async for item in self._yield_page_items(results, session_data):
                yield item
                scraped += 1
                if scraped >= limit:
                    return

            cursor = job_search.get("pageInfo", {}).get("nextCursor")
            if not cursor:
                break

            page_num += 1
            await asyncio.sleep(random.uniform(2.0, 5.0))  # noqa: S311

    async def parse(self, response: scrapy.http.Response) -> AsyncGenerator:
        session_data = response.meta.get("indeed_session")
        if not session_data:
            self.logger.error("No session available.")
            return

        async for item in self._scrape_jobs(session_data):
            yield item

        await self.graphql_client.close()
