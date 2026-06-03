import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_GRAPHQL_URL = "https://apis.indeed.com/graphql"
_APP_API_KEY = "161092c2017b5bbab13edb12461a62d5a833871e7cad6d9d475304573de67ac8"
_APP_BDT = (
    "7RJszkwn9Hz32YdO2u3BUCPeHlc12bk4o6tXAD26Na+K0IBFK9p/ijT7F/ahDlUBkYNONBtY93Ep"
    "mUWDO/QvYN92BTVH7lCWkQp2yxE6W8zE0mVI10eCEe5xV36ZAxiJ95FI2vNB6Xm2u4g="
)


class IndeedGraphQLClient:
    def __init__(
        self,
        locale: str = "pl-PL",
        country: str = "PL",
        proxy: str | None = None,
    ):
        self._locale = locale
        self._country = country
        self._client = httpx.AsyncClient(proxy=proxy, timeout=15)

        queries_dir = Path(__file__).parent.parent / "queries"
        with open(queries_dir / "viewjob.graphql", encoding="utf-8") as f:
            self.viewjob_query = f.read().strip()
        with open(queries_dir / "searchjobs.graphql", encoding="utf-8") as f:
            self.searchjobs_query = f.read().strip()

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _headers(self, session_data: dict) -> dict:
        return {
            "Host": "apis.indeed.com",
            "indeed-api-key": _APP_API_KEY,
            "indeed-ctk": session_data.get("ctk", ""),
            "Accept": "application/json",
            "indeed-locale": self._locale,
            "indeed-client-sub-app": "rnviewjob-ios",
            "Accept-Language": self._locale.split("-")[0],
            "User-Agent": "Indeed Jobs/41274 CFNetwork/3860.300.31 Darwin/25.2.0",
            "Indeed-App-Info": "appv=310.0; appid=com.indeed.jobsearch; osv=26.2; os=ios; dtype=tablet",
            "indeed-co": self._country,
            "Indeed-Device-ID": session_data.get("device_id", ""),
            "Indeed-BDT": _APP_BDT,
            "Content-Type": "application/json",
            "Cookie": session_data.get("cookie_string", ""),
        }

    async def test_session(self, session_data: dict) -> bool:
        query = """
        query JobSearch {
          jobSearch(what: "test", limit: 1) {
            results { job { key } }
          }
        }
        """
        try:
            res = await self._client.post(
                _GRAPHQL_URL,
                json={"query": query.strip()},
                headers=self._headers(session_data),
            )
            res.raise_for_status()
            data = res.json()
            for err in data.get("errors") or []:
                if any(
                    kw in err.get("message", "").lower()
                    for kw in ["auth", "permission", "unauthorized", "expired", "invalid"]
                ):
                    return False
            return True
        except Exception as e:
            logger.warning(f"Session test failed: {e}")
            return False

    async def search_jobs(
        self, query: str, location: str, cursor: str | None, session_data: dict
    ) -> dict | None:
        q = self.searchjobs_query
        q = q.replace("{what}", f'what: "{self._escape(query)}"' if query else "")
        q = q.replace(
            "{location}",
            f'location: {{where: "{self._escape(location)}", radius: 50, radiusUnit: MILES}}'
            if location else "",
        )
        q = q.replace("{cursor}", f'cursor: "{cursor}"' if cursor else "")
        q = q.replace("{filters}", "")

        logger.info(f"GraphQL search (query={query}, location={location}, cursor={cursor})")
        try:
            res = await self._client.post(
                _GRAPHQL_URL,
                json={"query": q.strip()},
                headers=self._headers(session_data),
            )
            res.raise_for_status()
            data = res.json()
            count = len(data.get("data", {}).get("jobSearch", {}).get("results", []))
            logger.info(f"Search completed. Results: {count}")
            return data
        except Exception as e:
            logger.error(f"Search error: {e}")
            self._maybe_clear_session(e, session_data)
            return None

    async def fetch_job(self, job_key: str, session_data: dict) -> dict | None:
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
        logger.info(f"GraphQL fetch job {job_key}")
        try:
            res = await self._client.post(
                _GRAPHQL_URL, json=payload, headers=self._headers(session_data)
            )
            res.raise_for_status()
            return res.json()
        except Exception as e:
            logger.error(f"Fetch job {job_key} error: {e}")
            self._maybe_clear_session(e, session_data)
            return None

    def _maybe_clear_session(self, exc: Exception, session_data: dict) -> None:
        is_auth_error = (
            isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403)
        ) or any(c in str(exc) for c in ("401", "403"))
        if not is_auth_error:
            return
        email = session_data.get("email")
        if not email:
            return
        path = Path("accounts") / f"{email}.json"
        if path.exists():
            try:
                path.unlink()
                logger.info(f"Deleted invalid session for {email}")
            except Exception as e:
                logger.error(f"Failed to delete session: {e}")
