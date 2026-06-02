import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from indeed_scraper.utils.graphql import IndeedGraphQLClient

logger = logging.getLogger(__name__)


def _build_item(
    jk: str,
    title: str | None,
    company: str | None,
    loc: str | None,
    search_data: dict,
    details_raw: dict | None,
) -> dict:
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
    apply_method = detail_info.get("applyMethod") or {}
    item["apply_url"] = apply_method.get("applyUrl") or apply_method.get("continueUrl")
    item["address"] = (detail_info.get("location") or {}).get("formattedStreetAddress")
    return item


async def scrape(
    session_data: dict,
    graphql_client: "IndeedGraphQLClient",
    query: str,
    location: str,
    limit: str | int = "all",
    output_dir: str = "data",
) -> Path:
    results_wanted = float("inf") if str(limit).lower() == "all" else int(limit)
    Path(output_dir).mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    output_file = Path(output_dir) / f"indeed_{timestamp}.json"

    items: list[dict] = []
    cursor = None
    page_num = 1
    scraped_count = 0

    while scraped_count < results_wanted:
        logger.info(f"Page {page_num} (scraped: {scraped_count})")

        result = await graphql_client.search_jobs(
            query=query, location=location, cursor=cursor, session_data=session_data
        )
        if not result:
            logger.error("Search returned empty.")
            break

        job_search = result.get("data", {}).get("jobSearch", {})
        results = job_search.get("results", [])
        if not results:
            logger.info("No more results.")
            break

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

            logger.info(f"[JOB] {jk} | {title} | {company} | {loc}")

            details_raw = await graphql_client.fetch_job(jk, session_data)
            items.append(_build_item(jk, title, company, loc, res_item, details_raw))

            scraped_count += 1
            if scraped_count >= results_wanted:
                break

            await asyncio.sleep(random.uniform(1.0, 2.0))  # noqa: S311

        cursor = job_search.get("pageInfo", {}).get("nextCursor")
        if not cursor:
            logger.info("No nextCursor, end of results.")
            break

        page_num += 1
        await asyncio.sleep(random.uniform(2.0, 5.0))  # noqa: S311

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    logger.info(f"Saved {len(items)} jobs to {output_file}")
    return output_file
