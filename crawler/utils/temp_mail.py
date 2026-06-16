import abc
import asyncio
import logging
import re
import secrets
import string
import time

import httpx

logger = logging.getLogger(__name__)


class EmailProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def provider_id(self) -> str: ...

    @abc.abstractmethod
    async def create_account(self) -> tuple[str, str]: ...

    @abc.abstractmethod
    async def wait_for_otp(
        self, token: str, timeout_seconds: int = 180, poll_interval: int = 5
    ) -> str | None: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _random_username(prefix: str = "", length: int = 10) -> str:
    chars = string.ascii_lowercase + string.digits
    return prefix + "".join(secrets.choice(chars) for _ in range(length))


def _extract_otp(text: str) -> str | None:
    m = re.search(r"\b\d{6}\b", text)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Provider: mail.tm
# ---------------------------------------------------------------------------


class _MailTmCompatibleProvider(EmailProvider):
    """Base class for mail.tm-compatible REST API services."""

    BASE_URL: str = ""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15
        )

    async def create_account(self) -> tuple[str, str]:
        res = await self._client.get(f"{self.BASE_URL}/domains")
        res.raise_for_status()
        domains = [d["domain"] for d in res.json().get("hydra:member", [])]
        if not domains:
            raise Exception(f"No active domains on {self.BASE_URL}")

        domain = secrets.choice(domains)
        address = f"{_random_username('indeed_')}@{domain}"
        password = "Tmp!Pass#99"

        resp = await self._client.post(
            f"{self.BASE_URL}/accounts",
            json={"address": address, "password": password},
        )
        resp.raise_for_status()

        token_res = await self._client.post(
            f"{self.BASE_URL}/token",
            json={"address": address, "password": password},
        )
        token_res.raise_for_status()
        return address, token_res.json().get("token")

    async def wait_for_otp(
        self, token: str, timeout_seconds: int = 180, poll_interval: int = 5
    ) -> str | None:
        seen_ids: set[str] = set()
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                res = await self._client.get(
                    f"{self.BASE_URL}/messages?page=1",
                    headers={"Authorization": f"Bearer {token}"},
                )
                res.raise_for_status()
                for msg in res.json().get("hydra:member", []):
                    msg_id = msg.get("id")
                    if msg_id in seen_ids:
                        continue
                    sender = msg.get("from", {}).get("address", "").lower()
                    subject = msg.get("subject", "").lower()
                    if any(kw in sender or kw in subject for kw in ["indeed", "verification", "confirm", "kod"]):
                        detail = await self._client.get(
                            f"{self.BASE_URL}/messages/{msg_id}",
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        detail.raise_for_status()
                        body = detail.json().get("text", "") or detail.json().get("html", "")
                        if otp := _extract_otp(body):
                            return otp
                    seen_ids.add(msg_id)
            except Exception as e:
                logger.warning(f"{self.provider_id} inbox check failed: {e}")
            await asyncio.sleep(poll_interval)
        return None


class MailTmProvider(_MailTmCompatibleProvider):
    BASE_URL = "https://api.mail.tm"

    @property
    def provider_id(self) -> str:
        return "mailtm"


class MailGwProvider(_MailTmCompatibleProvider):
    """mail.gw — independent service with same API as mail.tm, different domains."""

    BASE_URL = "https://api.mail.gw"

    @property
    def provider_id(self) -> str:
        return "mailgw"


# ---------------------------------------------------------------------------
# Provider: Guerrilla Mail (with domain rotation)
# ---------------------------------------------------------------------------

# Guerrilla Mail network domains — all work with the same API
_GUERRILLA_DOMAINS = [
    "guerrillamailblock.com",
    "guerrillamail.com",
    "guerrillamail.biz",
    "guerrillamail.de",
    "guerrillamail.net",
    "guerrillamail.org",
    "guerrillamail.info",
    "grr.la",
    "sharklasers.com",
    "spam4.me",
]


class GuerrillaMailProvider(EmailProvider):
    BASE_URL = "https://api.guerrillamail.com/ajax.php"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15
        )
        self._domain: str = ""

    @property
    def provider_id(self) -> str:
        return "guerrillamail"

    async def create_account(self) -> tuple[str, str]:
        # Pick a random domain from the guerrillamail network
        self._domain = secrets.choice(_GUERRILLA_DOMAINS)
        username = _random_username(length=10)

        res = await self._client.get(
            self.BASE_URL,
            params={
                "f": "set_email_user",
                "email_user": username,
                "lang": "en",
                "site": self._domain,
            },
        )
        res.raise_for_status()
        data = res.json()
        email = data.get("email_addr") or f"{username}@{self._domain}"
        sid_token = data.get("sid_token", "")
        if not sid_token:
            # Fallback: plain get_email_address
            res2 = await self._client.get(
                self.BASE_URL,
                params={"f": "get_email_address", "lang": "en"},
            )
            res2.raise_for_status()
            data2 = res2.json()
            email = data2["email_addr"]
            sid_token = data2["sid_token"]
        return email, sid_token

    async def wait_for_otp(
        self, sid_token: str, timeout_seconds: int = 180, poll_interval: int = 5
    ) -> str | None:
        seq = 0
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                res = await self._client.get(
                    self.BASE_URL,
                    params={"f": "check_email", "seq": seq, "sid_token": sid_token},
                )
                res.raise_for_status()
                for msg in res.json().get("list", []):
                    sender = msg.get("mail_from", "").lower()
                    subject = msg.get("mail_subject", "").lower()
                    if any(kw in sender or kw in subject for kw in ["indeed", "verification", "confirm"]):
                        mail_id = str(msg.get("mail_id", ""))
                        detail = await self._client.get(
                            self.BASE_URL,
                            params={"f": "fetch_email", "email_id": mail_id, "sid_token": sid_token},
                        )
                        detail.raise_for_status()
                        body = detail.json().get("mail_body", "")
                        if otp := _extract_otp(body):
                            return otp
                    seq = max(seq, int(msg.get("mail_id", 0)))
            except Exception as e:
                logger.warning(f"GuerrillaMailProvider inbox check failed: {e}")
            await asyncio.sleep(poll_interval)
        return None


# ---------------------------------------------------------------------------
# Provider: Mailnesia
# ---------------------------------------------------------------------------


class MailnesiaProvider(EmailProvider):
    """mailnesia.com — автоматический inbox без регистрации."""

    BASE_URL = "https://mailnesia.com"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15
        )
        self._username: str = ""

    @property
    def provider_id(self) -> str:
        return "mailnesia"

    async def create_account(self) -> tuple[str, str]:
        self._username = _random_username(length=14)
        address = f"{self._username}@mailnesia.com"
        return address, self._username  # token = username

    async def wait_for_otp(
        self, token: str, timeout_seconds: int = 180, poll_interval: int = 5
    ) -> str | None:
        deadline = time.time() + timeout_seconds
        seen: set[str] = set()
        while time.time() < deadline:
            try:
                res = await self._client.get(f"{self.BASE_URL}/mailbox/{token}")
                res.raise_for_status()
                # parse links to individual emails
                email_links = re.findall(
                    rf"/mailbox/{re.escape(token)}/(\d+)", res.text
                )
                for eid in email_links:
                    if eid in seen:
                        continue
                    seen.add(eid)
                    detail = await self._client.get(
                        f"{self.BASE_URL}/mailbox/{token}/{eid}"
                    )
                    if otp := _extract_otp(detail.text):
                        return otp
            except Exception as e:
                logger.warning(f"MailnesiaProvider inbox check failed: {e}")
            await asyncio.sleep(poll_interval)
        return None


# ---------------------------------------------------------------------------
# Provider: Dispostable
# ---------------------------------------------------------------------------


class DispostableProvider(EmailProvider):
    """dispostable.com — одноразовые адреса без регистрации."""

    BASE_URL = "https://www.dispostable.com"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15
        )

    @property
    def provider_id(self) -> str:
        return "dispostable"

    async def create_account(self) -> tuple[str, str]:
        username = _random_username(length=12)
        address = f"{username}@dispostable.com"
        return address, username

    async def wait_for_otp(
        self, token: str, timeout_seconds: int = 180, poll_interval: int = 5
    ) -> str | None:
        deadline = time.time() + timeout_seconds
        seen: set[str] = set()
        while time.time() < deadline:
            try:
                res = await self._client.get(
                    f"{self.BASE_URL}/inbox/{token}",
                    params={"format": "json"},
                )
                res.raise_for_status()
                messages = res.json().get("messages", [])
                for msg in messages:
                    msg_id = str(msg.get("id", ""))
                    if msg_id in seen:
                        continue
                    seen.add(msg_id)
                    sender = msg.get("sender", "").lower()
                    subject = msg.get("subject", "").lower()
                    if any(kw in sender or kw in subject for kw in ["indeed", "verification", "confirm"]):
                        body = msg.get("body", "")
                        if otp := _extract_otp(body):
                            return otp
            except Exception as e:
                logger.warning(f"DispostableProvider inbox check failed: {e}")
            await asyncio.sleep(poll_interval)
        return None


# ---------------------------------------------------------------------------
# Provider: Inbox Kitten
# ---------------------------------------------------------------------------


class InboxKittenProvider(EmailProvider):
    """inboxkitten.com — serverless одноразовая почта на базе Mailgun."""

    BASE_URL = "https://inboxkitten.com/api"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15
        )

    @property
    def provider_id(self) -> str:
        return "inboxkitten"

    async def create_account(self) -> tuple[str, str]:
        username = _random_username(length=12)
        address = f"{username}@inboxkitten.com"
        return address, username

    async def wait_for_otp(
        self, token: str, timeout_seconds: int = 180, poll_interval: int = 5
    ) -> str | None:
        deadline = time.time() + timeout_seconds
        seen: set[str] = set()
        while time.time() < deadline:
            try:
                res = await self._client.get(
                    f"{self.BASE_URL}/v1/inbox/retrieve",
                    params={"emailName": token, "domain": "inboxkitten.com"},
                )
                res.raise_for_status()
                for msg in res.json():
                    msg_id = str(msg.get("timestamp", ""))
                    if msg_id in seen:
                        continue
                    seen.add(msg_id)
                    sender = (msg.get("sender") or "").lower()
                    subject = (msg.get("subject") or "").lower()
                    if any(kw in sender or kw in subject for kw in ["indeed", "verification", "confirm"]):
                        body = msg.get("body-plain", "") or msg.get("stripped-text", "")
                        if otp := _extract_otp(body):
                            return otp
            except Exception as e:
                logger.warning(f"InboxKittenProvider inbox check failed: {e}")
            await asyncio.sleep(poll_interval)
        return None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class TempMailManager:
    def __init__(self, providers: list[EmailProvider]) -> None:
        self._providers = providers

    async def create_new_email(self, skip_providers: list[str] | None = None) -> tuple[str, str]:
        """
        Try each provider in order, skipping any whose provider_id is in *skip_providers*.
        Raises if no provider is available.
        """
        skip = set(skip_providers or [])
        for provider in self._providers:
            if provider.provider_id in skip:
                continue
            try:
                email, raw_token = await provider.create_account()
                logger.info(f"Created email via {provider.provider_id}: {email}")
                return email, f"{provider.provider_id}:{raw_token}"
            except Exception as e:
                logger.warning(f"{provider.provider_id} failed: {e}")
        raise Exception("All email providers exhausted")

    async def wait_for_otp_code(
        self, encoded_token: str, timeout_seconds: int = 180, poll_interval: int = 5
    ) -> str | None:
        name, raw_token = encoded_token.split(":", 1)
        for provider in self._providers:
            if provider.provider_id == name:
                return await provider.wait_for_otp(raw_token, timeout_seconds, poll_interval)
        raise Exception(f"Unknown provider: {name}")
