import asyncio
import logging
import os
import re
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


class IndeedGraphQLClient:
    def __init__(
        self,
        api_key: str = "",
        bdt_token: str = "",
        locale: str = "pl-PL",
        country: str = "PL",
    ):
        self._api_key = api_key or os.environ.get("INDEED_API_KEY", "")
        self._bdt_token = bdt_token or os.environ.get("INDEED_BDT", "")
        self._locale = locale
        self._country = country

        queries_dir = Path(__file__).parent.parent / "queries"
        viewjob_file = queries_dir / "viewjob.graphql"
        try:
            with open(viewjob_file, encoding="utf-8") as f:
                self.viewjob_query = f.read().strip()
        except Exception as e:
            logger.critical(f"Failed to read viewjob.graphql from {viewjob_file}: {e}")
            raise e

        searchjobs_file = queries_dir / "searchjobs.graphql"
        try:
            with open(searchjobs_file, encoding="utf-8") as f:
                self.searchjobs_query = f.read().strip()
        except Exception as e:
            logger.critical(
                f"Failed to read searchjobs.graphql from {searchjobs_file}: {e}"
            )
            raise e

    def _build_headers(self, session_data: dict) -> dict:
        return {
            "Host": "apis.indeed.com",
            "indeed-api-key": self._api_key,
            "indeed-ctk": session_data["ctk"],
            "Accept": "application/json",
            "indeed-locale": self._locale,
            "indeed-client-sub-app": "rnviewjob-ios",
            "Accept-Language": self._locale.split("-")[0],
            "User-Agent": "Indeed Jobs/41274 CFNetwork/3860.300.31 Darwin/25.2.0",
            "Indeed-App-Info": "appv=310.0; appid=com.indeed.jobsearch; osv=26.2; os=ios; dtype=tablet",
            "indeed-co": self._country,
            "Indeed-Device-ID": session_data.get("device_id", "1jp0pgc73hmla803"),
            "Indeed-BDT": self._bdt_token,
            "Content-Type": "application/json",
            "Cookie": session_data["cookie_string"],
        }

    async def test_session(self, session_data: dict) -> bool:
        def _post() -> bool:
            query_str = """
            query JobSearch {
              jobSearch(
                what: "test"
                limit: 1
              ) {
                results {
                  job {
                    key
                  }
                }
              }
            }
            """
            payload = {"query": query_str.strip()}
            res = requests.post(
                "https://apis.indeed.com/graphql",
                json=payload,
                headers=self._build_headers(session_data),
                timeout=10,
            )
            res.raise_for_status()
            data = res.json()
            if "errors" in data and data["errors"]:
                for err in data["errors"]:
                    message = err.get("message", "").lower()
                    if any(
                        kw in message
                        for kw in ["auth", "permission", "unauthorized", "expired", "invalid"]
                    ):
                        return False
            return True

        try:
            return await asyncio.to_thread(_post)
        except Exception as e:
            logger.warning(f"Session test failed for email {session_data.get('email')}: {e}")
            return False

    async def search_jobs(
        self, query: str, location: str, cursor: str | None, session_data: dict
    ) -> dict | None:
        def _post() -> dict:
            query_str = self.searchjobs_query
            query_str = query_str.replace("{what}", f'what: "{query}"' if query else "")

            loc_str = (
                f'location: {{where: "{location}", radius: 50, radiusUnit: MILES}}'
                if location
                else ""
            )
            query_str = query_str.replace("{location}", loc_str)
            query_str = query_str.replace(
                "{cursor}", f'cursor: "{cursor}"' if cursor else ""
            )
            query_str = query_str.replace("{filters}", "")

            res = requests.post(
                "https://apis.indeed.com/graphql",
                json={"query": query_str.strip()},
                headers=self._build_headers(session_data),
                timeout=15,
            )
            res.raise_for_status()
            data = res.json()
            results_count = len(
                data.get("data", {}).get("jobSearch", {}).get("results", [])
            )
            logger.info(f"GraphQL search completed. Results count: {results_count}")
            return data

        logger.info(
            f"GraphQL job search (query={query}, location={location}, cursor={cursor})..."
        )
        try:
            return await asyncio.to_thread(_post)
        except Exception as e:
            logger.error(f"GraphQL search error: {e}")
            if isinstance(e, requests.HTTPError) and e.response is not None:
                if e.response.status_code in (401, 403):
                    self._clear_session(session_data)
            elif any(code in str(e).lower() for code in ("401", "403")):
                self._clear_session(session_data)
            return None

    async def fetch_job(self, job_key: str, session_data: dict) -> dict | None:
        def _post() -> dict:
            payload = {
                "query": self.viewjob_query,
                "variables": {
                    "input": job_key,
                    "enableEmployerInsights": False,
                    "jobResultTrackingKey": None,
                    "detailsInput": {
                        "viewjobCookieModel": {
                            "jobseekerCookieModel": {},
                            "clickTrackingLog": f"jk={job_key}&previousPageNumber=1,last",
                            "previousPageNumber": "1,last",
                        },
                        "viewjobUrl": f"https://www.indeed.com/m/viewjob?jk={job_key}",
                        "shouldQueryApplyRateLimit": True,
                    },
                    "isLoggedIn": False,
                },
            }

            res = requests.post(
                "https://apis.indeed.com/graphql",
                json=payload,
                headers=self._build_headers(session_data),
                timeout=15,
            )
            res.raise_for_status()
            return res.json()

        logger.info(f"GraphQL fetch job {job_key}...")
        try:
            return await asyncio.to_thread(_post)
        except Exception as e:
            logger.error(f"GraphQL fetch job {job_key} error: {e}")
            if isinstance(e, requests.HTTPError) and e.response is not None:
                if e.response.status_code in (401, 403):
                    self._clear_session(session_data)
            elif any(code in str(e).lower() for code in ("401", "403")):
                self._clear_session(session_data)
            return None

    async def get_total_job_count(
        self, session_data: dict, query: str, location: str
    ) -> int | None:
        def _fetch() -> str | None:
            url = "https://pl.indeed.com/jobs"
            params = {"q": query, "l": location}
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
                "Cookie": session_data["cookie_string"],
            }
            try:
                res = requests.get(url, params=params, headers=headers, timeout=15)
                res.raise_for_status()
                return res.text
            except Exception as e:
                logger.warning(f"Failed to fetch indeed search page: {e}")
                return None

        html = await asyncio.to_thread(_fetch)
        if not html:
            return None

        match = re.search(r'"totalJobCount":\s*(\d+)', html)
        if match:
            return int(match.group(1))
        match = re.search(r'"totalNumResults":\s*(\d+)', html)
        if match:
            return int(match.group(1))
        return None

    def _clear_session(self, session_data: dict) -> None:
        email = session_data.get("email")
        if not email:
            return
        account_file = Path("accounts") / f"{email}.json"
        if account_file.exists():
            try:
                account_file.unlink()
                logger.info(
                    f"Session file for {email} deleted due to auth error (401/403)."
                )
            except Exception as del_err:
                logger.error(f"Failed to delete session file for {email}: {del_err}")
