import json
import logging
import random
from pathlib import Path

from scrapy.exceptions import CloseSpider, IgnoreRequest
from scrapy.downloadermiddlewares.retry import get_retry_request

from crawler.utils.auth import create_new_account

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
                spider.logger.info(f"Loaded session for: {email}")
                self._accounts.append(session_data)
            except (json.JSONDecodeError, OSError):
                spider.logger.exception(f"Error loading account {acc_file.name}")

        target_count = getattr(spider, "accounts_count", None) or spider.settings.getint("ACCOUNTS_COUNT", 3)
        current_count = len(self._accounts)

        if current_count < target_count:
            needed = target_count - current_count
            spider.logger.info(
                f"Loaded {current_count} accounts. Creating {needed} more to meet target count of {target_count}..."
            )
            for _ in range(needed):
                new_acc = await create_new_account(self.accounts_dir, spider.logger)
                if new_acc:
                    self._accounts.append(new_acc)
                else:
                    raise CloseSpider("Failed to create required new account (login or CAPTCHA failed).")

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
                    except OSError:
                        spider.logger.exception(f"Failed to delete session file: {account_file}")

            # Retry request with a different account
            retry_req = get_retry_request(
                request, spider=spider, reason="unauthorized_session"
            )
            if retry_req:
                return retry_req
            else:
                raise IgnoreRequest("Request failed after exhausting retry attempts.")

        return response
