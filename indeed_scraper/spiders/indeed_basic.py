import asyncio
import random
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import scrapy
from browserforge.fingerprints import Screen

from indeed_scraper.items import IndeedJobItem
from indeed_scraper.utils.auth import perform_login_flow
from indeed_scraper.utils.indeed_graphql import IndeedGraphQLClient
from indeed_scraper.utils.temp_mail import TempMailManager

if TYPE_CHECKING:
    from playwright.async_api import Page


def _build_item(
    jk: str,
    title: str | None,
    company: str | None,
    loc: str | None,
    search_data: dict,
    details_raw: dict | None,
) -> IndeedJobItem:
    item = IndeedJobItem(
        job_key=jk,
        title=title,
        company=company,
        location=loc,
        search_data=search_data,
    )

    if not details_raw:
        return item

    viewjob = (details_raw.get("data") or {}).get("viewjob") or {}
    detail_job = viewjob.get("job") or {}
    detail_info = viewjob.get("details") or {}
    comp = detail_job.get("compensation") or {}
    base_salary = comp.get("baseSalary") or {}

    item["description"] = (detail_job.get("description") or {}).get("text")
    item["date_published"] = detail_job.get("datePublished")
    item["salary"] = comp.get("formattedText")
    item["salary_min"] = base_salary.get("range", {}).get("min") if base_salary else None
    item["salary_max"] = base_salary.get("range", {}).get("max") if base_salary else None
    item["remote"] = (detail_info.get("remoteWorkModel") or {}).get("text")
    item["job_types"] = (detail_info.get("jobTypeAndShiftSchedule") or {}).get("jobTypes") or []
    item["benefits"] = [
        b.get("label")
        for b in (detail_info.get("benefit") or {}).get("benefits") or []
    ]
    apply_method = detail_info.get("applyMethod") or {}
    item["apply_url"] = apply_method.get("applyUrl") or apply_method.get("continueUrl")
    item["is_remote"] = (detail_info.get("remoteWorkModel") or {}).get("type")
    item["address"] = (detail_info.get("location") or {}).get("formattedStreetAddress")

    return item


class IndeedBasicSpider(scrapy.Spider):
    name = "indeed_basic"

    handle_httpstatus_list = [403, 999]

    custom_settings = {
        "LOG_LEVEL": "INFO",
        "LOG_ENABLED": True,
        "COOKIES_ENABLED": True,
        "DOWNLOAD_HANDLERS": {
            "http": "indeed_scraper.playfox_handler.CustomPlayfoxDownloadHandler",
            "https": "indeed_scraper.playfox_handler.CustomPlayfoxDownloadHandler",
        },
        "PLAYWRIGHT_LAUNCH_OPTIONS": {
            "headless": False,
            "humanize": True,
            "screen": Screen(max_width=1280, max_height=800),
            "geoip": False,
            "disable_coop": True,
        },
    }

    def __init__(
        self,
        query: str = "python",
        location: str = "Warszawa",
        force_login: bool = False,
        limit: str = "all",
        **kwargs: str,
    ):
        super().__init__(**kwargs)
        self.query = query
        self.location = location
        self.force_login = str(force_login).lower() in ("true", "1", "yes")
        self.limit = limit
        self.mail_manager = TempMailManager()
        self.graphql_client = IndeedGraphQLClient()

    @classmethod
    def from_crawler(cls, crawler: scrapy.crawler.Crawler, *args: object, **kwargs: object) -> "IndeedBasicSpider":
        spider: IndeedBasicSpider = super().from_crawler(crawler, *args, **kwargs)
        spider.graphql_client = IndeedGraphQLClient(
            api_key=crawler.settings.get("INDEED_API_KEY", ""),
            bdt_token=crawler.settings.get("INDEED_BDT", ""),
        )
        return spider

    async def start(self) -> AsyncGenerator:
        yield scrapy.Request(
            url="https://pl.indeed.com/",
            callback=self.parse,
            meta={
                "playwright": True,
                "playwright_context": "indeed",
                "playwright_context_kwargs": {"user_data_dir": "playfox_data"},
                "playwright_include_page": True,
                "requires_auth": True,
            },
            dont_filter=True,
        )

    async def perform_login_flow(self, page: "Page", user_data_dir: str | None = None) -> dict:
        api_key = self.settings.get("CAPTCHA_API_KEY", "")
        return await perform_login_flow(page, self.mail_manager, user_data_dir, api_key)

    def _resolve_results_wanted(self) -> float:
        if str(self.limit).lower() == "all":
            return float("inf")
        try:
            return int(self.limit)
        except ValueError:
            return 30

    async def _yield_page_items(
        self, results: list[dict], session_data: dict
    ) -> AsyncGenerator:
        for res_item in results:
            job = res_item.get("job")
            if not job:
                continue
            jk = job.get("key")
            title = job.get("title")
            employer = job.get("employer") or {}
            company = job.get("sourceEmployerName") or employer.get(
                "parentEmployer", {}
            ).get("name")
            loc = (job.get("location") or {}).get("formatted", {}).get("long")
            self.logger.info(f"[JOB] {jk} | {title} | {company} | {loc}")
            details_raw = await self.graphql_client.fetch_job(jk, session_data)
            yield _build_item(jk, title, company, loc, res_item, details_raw)
            await asyncio.sleep(random.uniform(1.0, 2.0))  # noqa: S311

    async def _scrape_jobs(self, session_data: dict) -> AsyncGenerator:
        cursor = None
        page_num = 1
        scraped_count = 0
        results_wanted = self._resolve_results_wanted()
        self.logger.info(f"Will scrape up to {results_wanted} jobs.")

        while scraped_count < results_wanted:
            self.logger.info(f"GraphQL page {page_num} (scraped: {scraped_count})")

            result = await self.graphql_client.search_jobs(
                query=self.query, location=self.location,
                cursor=cursor, session_data=session_data,
            )
            if not result:
                self.logger.error("GraphQL search returned empty.")
                break

            job_search = result.get("data", {}).get("jobSearch", {})
            results = job_search.get("results", [])
            if not results:
                self.logger.info(f"No more jobs on page {page_num}.")
                break

            async for item in self._yield_page_items(results, session_data):
                yield item
                scraped_count += 1
                if scraped_count >= results_wanted:
                    return

            cursor = job_search.get("pageInfo", {}).get("nextCursor")
            if not cursor:
                self.logger.info("No nextCursor, end of results.")
                break

            page_num += 1
            await asyncio.sleep(random.uniform(2.0, 5.0))  # noqa: S311

    async def parse(self, response: scrapy.http.Response) -> AsyncGenerator:
        session_data = response.meta.get("indeed_session")
        page = response.meta.get("playwright_page")

        if page:
            try:
                await page.close()
            except Exception as e:
                self.logger.warning(f"Failed to close playwright page: {e}")

        if not session_data:
            self.logger.error("No indeed session available.")
            return

        async for item in self._scrape_jobs(session_data):
            yield item
