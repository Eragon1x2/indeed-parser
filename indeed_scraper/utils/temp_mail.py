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
    @abc.abstractmethod
    async def create_account(self) -> tuple[str, str]: ...

    @abc.abstractmethod
    async def wait_for_otp(
        self, token: str, timeout_seconds: int = 180, poll_interval: int = 5
    ) -> str | None: ...


class MailTmProvider(EmailProvider):
    BASE_URL = "https://api.mail.tm"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15
        )

    async def create_account(self) -> tuple[str, str]:
        res = await self._client.get(f"{self.BASE_URL}/domains")
        res.raise_for_status()
        domains = [d["domain"] for d in res.json().get("hydra:member", [])]
        if not domains:
            raise Exception("No active domains on mail.tm")

        domain = secrets.choice(domains)
        chars = string.ascii_lowercase + string.digits
        username = "indeed_" + "".join(secrets.choice(chars) for _ in range(8))
        address = f"{username}@{domain}"
        password = "Tmp!Pass#99"

        await (await self._client.post(
            f"{self.BASE_URL}/accounts",
            json={"address": address, "password": password},
        )).raise_for_status()

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
                        match = re.search(r"\b\d{6}\b", body)
                        if match:
                            return match.group(0)
                    seen_ids.add(msg_id)
            except Exception as e:
                logger.warning(f"MailTmProvider inbox check failed: {e}")
            await asyncio.sleep(poll_interval)
        return None


class GuerrillaMailProvider(EmailProvider):
    BASE_URL = "https://api.guerrillamail.com/ajax.php"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15
        )

    async def create_account(self) -> tuple[str, str]:
        res = await self._client.get(
            self.BASE_URL,
            params={"f": "get_email_address", "lang": "en", "ip": "127.0.0.1", "agent": "Mozilla_4.0"},
        )
        res.raise_for_status()
        data = res.json()
        return data["email_addr"], data["sid_token"]

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
                        match = re.search(r"\b\d{6}\b", body)
                        if match:
                            return match.group(0)
                    seq = max(seq, int(msg.get("mail_id", 0)))
            except Exception as e:
                logger.warning(f"GuerrillaMailProvider inbox check failed: {e}")
            await asyncio.sleep(poll_interval)
        return None


class TempMailManager:
    def __init__(self) -> None:
        self._providers: list[EmailProvider] = [
            MailTmProvider(),
            GuerrillaMailProvider(),
        ]

    async def create_new_email(self, skip_providers: list[str] | None = None) -> tuple[str, str]:
        for provider in self._providers:
            if skip_providers and type(provider).__name__ in skip_providers:
                continue
            try:
                email, raw_token = await provider.create_account()
                logger.info(f"Created email via {type(provider).__name__}: {email}")
                return email, f"{type(provider).__name__}:{raw_token}"
            except Exception as e:
                logger.warning(f"{type(provider).__name__} failed: {e}")
        raise Exception("All email providers exhausted")

    async def wait_for_otp_code(
        self, encoded_token: str, timeout_seconds: int = 180, poll_interval: int = 5
    ) -> str | None:
        name, raw_token = encoded_token.split(":", 1)
        for provider in self._providers:
            if type(provider).__name__ == name:
                return await provider.wait_for_otp(raw_token, timeout_seconds, poll_interval)
        raise Exception(f"Unknown provider: {name}")
