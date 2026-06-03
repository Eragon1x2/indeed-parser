import json
import logging
import os
import secrets
from pathlib import Path

from browserforge.fingerprints import Screen
from camoufox.async_api import AsyncNewBrowser
from playwright.async_api import async_playwright
from playwright_captcha.utils.camoufox_add_init_script.add_init_script import get_addon_path
from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse

logger = logging.getLogger(__name__)

_CRAWLER_DIR = Path(__file__).parent.parent


class IndeedSessionMiddleware:
    def __init__(self, crawler) -> None:
        self.accounts_dir = _CRAWLER_DIR / "accounts"
        self._crawler = crawler

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    @property
    def _spider(self):
        return self._crawler.spider

    async def process_request(self, request, spider=None):
        if not request.meta.get("requires_auth"):
            return None

        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        sp = self._spider or spider
        force_login = getattr(sp, "force_login", False)
        account_files = [] if force_login else list(self.accounts_dir.glob("*.json"))

        sp.logger.info(f"Found {len(account_files)} cached account(s) (force_login={force_login}).")

        for account_file in account_files:
            try:
                with open(account_file, encoding="utf-8") as f:
                    session_data = json.load(f)
                email = session_data.get("email")
                sp.logger.info(f"Testing session for {email}...")
                if await sp.graphql_client.test_session(session_data):
                    sp.logger.info(f"Session for {email} is VALID.")
                    return self._make_response(request, session_data)
                sp.logger.warning(f"Session for {email} is INVALID/EXPIRED.")
            except Exception as e:
                sp.logger.warning(f"Failed to load session from {account_file.name}: {e}")

        sp.logger.info("No valid session. Launching browser...")
        try:
            user_data_dir = str(_CRAWLER_DIR / "playfox_data" / f"account_{secrets.token_hex(4)}")
            addon_path = get_addon_path()

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
                )

                page = context.pages[0] if context.pages else await context.new_page()
                sp.logger.info("Navigating to Indeed auth page...")
                await page.goto("https://secure.indeed.com/auth?hl=pl")

                session_data = await sp.perform_login_flow(page, user_data_dir=user_data_dir)

                email = session_data.get("email")
                if not email:
                    raise Exception("No email in session data.")

                account_file = self.accounts_dir / f"{email}.json"
                with open(account_file, "w", encoding="utf-8") as f:
                    json.dump(session_data, f, ensure_ascii=False, indent=2)
                sp.logger.info(f"Session saved to {account_file}")

                await context.close()
                return self._make_response(request, session_data)

        except Exception as e:
            sp.logger.error(f"Critical error during auth: {e}")
            raise IgnoreRequest(f"Failed to authenticate: {e}")

    async def process_response(self, request, response, spider=None):
        if not request.meta.get("requires_auth"):
            return response

        if response.status in (401, 403, 999):
            sp = self._spider or spider
            sp.logger.warning(f"Received status {response.status}. Session may be invalid.")
            session_data = request.meta.get("indeed_session")
            if session_data:
                email = session_data.get("email")
                if email:
                    account_file = self.accounts_dir / f"{email}.json"
                    if account_file.exists():
                        try:
                            account_file.unlink()
                            sp.logger.info(f"Deleted invalid session: {account_file}")
                        except Exception as e:
                            sp.logger.error(f"Failed to delete session: {e}")
            raise IgnoreRequest("Session invalid, aborting.")

        return response

    @staticmethod
    def _make_response(request, session_data: dict) -> HtmlResponse:
        response = HtmlResponse(
            url=request.url,
            status=200,
            headers=request.headers,
            body=b"",
            encoding="utf-8",
            request=request,
        )
        response.meta["indeed_session"] = session_data
        return response
