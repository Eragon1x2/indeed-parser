import json
import logging
import os
import random
import secrets
from pathlib import Path

import httpx
from browserforge.fingerprints import Screen
from camoufox.async_api import AsyncNewBrowser
from playwright.async_api import async_playwright
from playwright_captcha.utils.camoufox_add_init_script.add_init_script import get_addon_path
from scrapy.exceptions import IgnoreRequest
from scrapy.downloadermiddlewares.retry import get_retry_request

from config import settings

logger = logging.getLogger(__name__)

_CRAWLER_DIR = Path(__file__).parent.parent


class IndeedSessionMiddleware:
    def __init__(self, crawler) -> None:
        self.accounts_dir = _CRAWLER_DIR / "accounts"
        self._crawler = crawler
        self._initialized = False
        self._accounts = []

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    async def _test_session(self, session_data: dict) -> bool:
        query = """
        query JobSearch {
          jobSearch(what: "test", limit: 1) {
            results { job { key } }
          }
        }
        """
        headers = {
            "Host": "apis.indeed.com",
            "indeed-api-key": "161092c2017b5bbab13edb12461a62d5a833871e7cad6d9d475304573de67ac8",
            "indeed-ctk": session_data.get("ctk", ""),
            "Accept": "application/json",
            "indeed-locale": "pl-PL",
            "indeed-client-sub-app": "rnviewjob-ios",
            "Accept-Language": "pl",
            "User-Agent": "Indeed Jobs/41274 CFNetwork/3860.300.31 Darwin/25.2.0",
            "Indeed-App-Info": "appv=310.0; appid=com.indeed.jobsearch; osv=26.2; os=ios; dtype=tablet",
            "indeed-co": "PL",
            "Indeed-Device-ID": session_data.get("device_id", ""),
            "Indeed-BDT": "7RJszkwn9Hz32YdO2u3BUCPeHlc12bk4o6tXAD26Na+K0IBFK9p/ijT7F/ahDlUBkYNONBtY93EpmUWDO/QvYN92BTVH7lCWkQp2yxE6W8zE0mVI10eCEe5xV36ZAxiJ95FI2vNB6Xm2u4g=",
            "Content-Type": "application/json",
            "Cookie": session_data.get("cookie_string", ""),
        }
        proxy = str(settings.scraper.proxy) if settings.scraper.proxy else None
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=15) as client:
                res = await client.post(
                    "https://apis.indeed.com/graphql",
                    json={"query": query.strip()},
                    headers=headers,
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

    async def _create_new_account(self, spider) -> dict | None:
        spider.logger.info("Initializing new account login flow via Playwright...")
        try:
            user_data_dir = str(
                _CRAWLER_DIR / "playfox_data" / f"account_{secrets.token_hex(4)}"
            )
            addon_path = get_addon_path()

            proxy_args = {}
            if settings.scraper.proxy:
                proxy_args["proxy"] = {"server": str(settings.scraper.proxy)}

            async with async_playwright() as p:
                context = await AsyncNewBrowser(
                    playwright=p,
                    headless=False,
                    humanize=True,
                    screen=Screen(max_width=1280, max_height=800),
                    geoip=False,
                    disable_coop=True,
                    persistent_context=True,
                    user_data_dir=user_data_dir,
                    main_world_eval=True,
                    addons=[os.path.abspath(addon_path)],
                    i_know_what_im_doing=True,
                    config={"forceScopeAccess": True},
                    **proxy_args,
                )

                page = context.pages[0] if context.pages else await context.new_page()
                spider.logger.info("Navigating to Indeed auth page...")
                await page.goto("https://secure.indeed.com/auth?hl=pl")

                from crawler.utils.auth import perform_login_flow
                from crawler.utils.temp_mail import TempMailManager

                mail_manager = TempMailManager()
                session_data = await perform_login_flow(
                    page, mail_manager, user_data_dir, settings.captcha_api_key
                )

                email = session_data.get("email")
                if not email:
                    raise Exception("No email in session data.")

                account_file = self.accounts_dir / f"{email}.json"
                with open(account_file, "w", encoding="utf-8") as f:
                    json.dump(session_data, f, ensure_ascii=False, indent=2)
                spider.logger.info(f"Saved session to {account_file}")

                await context.close()
                return session_data
        except Exception as e:
            spider.logger.error(f"Failed to create new account: {e}")
            return None

    async def _initialize_accounts(self, spider):
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        account_files = list(self.accounts_dir.glob("*.json"))

        spider.logger.info(
            f"Loading accounts. Found {len(account_files)} files in config directory."
        )

        for acc_file in account_files:
            try:
                with open(acc_file, encoding="utf-8") as f:
                    session_data = json.load(f)
                email = session_data.get("email")
                spider.logger.info(f"Checking session validity for: {email}")
                if await self._test_session(session_data):
                    spider.logger.info(f"Session for {email} is VALID.")
                    self._accounts.append(session_data)
                else:
                    spider.logger.warning(
                        f"Session for {email} is EXPIRED/INVALID. Re-authenticating..."
                    )
                    new_acc = await self._create_new_account(spider)
                    if new_acc:
                        self._accounts.append(new_acc)
            except Exception as e:
                spider.logger.error(f"Error loading account {acc_file.name}: {e}")

        if not self._accounts:
            spider.logger.warning("No valid accounts in directory. Creating a new one...")
            new_acc = await self._create_new_account(spider)
            if new_acc:
                self._accounts.append(new_acc)

    async def process_request(self, request, spider):
        if not request.meta.get("requires_auth"):
            return None

        if not self._initialized:
            await self._initialize_accounts(spider)
            self._initialized = True

        if not self._accounts:
            spider.logger.error("No valid accounts available. Aborting request.")
            raise IgnoreRequest("No valid accounts available.")

        # Rotate account
        account = random.choice(self._accounts)

        # Inject headers and cookies
        request.headers["Host"] = "apis.indeed.com"
        request.headers["indeed-api-key"] = (
            "161092c2017b5bbab13edb12461a62d5a833871e7cad6d9d475304573de67ac8"
        )
        request.headers["indeed-ctk"] = account.get("ctk", "")
        request.headers["Accept"] = "application/json"
        request.headers["indeed-locale"] = "pl-PL"
        request.headers["indeed-client-sub-app"] = "rnviewjob-ios"
        request.headers["Accept-Language"] = "pl"
        request.headers["User-Agent"] = (
            "Indeed Jobs/41274 CFNetwork/3860.300.31 Darwin/25.2.0"
        )
        request.headers["Indeed-App-Info"] = (
            "appv=310.0; appid=com.indeed.jobsearch; osv=26.2; os=ios; dtype=tablet"
        )
        request.headers["indeed-co"] = "PL"
        request.headers["Indeed-Device-ID"] = account.get("device_id", "")
        request.headers["Indeed-BDT"] = (
            "7RJszkwn9Hz32YdO2u3BUCPeHlc12bk4o6tXAD26Na+K0IBFK9p/ijT7F/ahDlUBkYNONBtY93EpmUW"
            "DO/QvYN92BTVH7lCWkQp2yxE6W8zE0mVI10eCEe5xV36ZAxiJ95FI2vNB6Xm2u4g="
        )
        request.headers["Content-Type"] = "application/json"
        request.headers["Cookie"] = account.get("cookie_string", "")

        request.meta["indeed_account"] = account
        return None

    async def process_response(self, request, response, spider):
        if not request.meta.get("requires_auth"):
            return response

        if response.status in (401, 403, 999):
            account = request.meta.get("indeed_account")
            if account:
                email = account.get("email")
                spider.logger.warning(
                    f"Account {email} session failed (status {response.status}). Removing from rotation."
                )
                self._accounts = [a for a in self._accounts if a.get("email") != email]

                account_file = self.accounts_dir / f"{email}.json"
                if account_file.exists():
                    try:
                        account_file.unlink()
                        spider.logger.info(f"Deleted invalid session file: {account_file}")
                    except Exception as e:
                        spider.logger.error(f"Failed to delete session file: {e}")

            # Retry request with a different account
            retry_req = get_retry_request(
                request, spider=spider, reason="unauthorized_session"
            )
            if retry_req:
                return retry_req
            else:
                raise IgnoreRequest("Request failed after exhausting retry attempts.")

        return response
