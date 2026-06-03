import asyncio
import logging

from indeed_scraper.config import settings
from indeed_scraper.scraper import scrape
from indeed_scraper.session import get_session
from indeed_scraper.utils.graphql import IndeedGraphQLClient
from indeed_scraper.utils.temp_mail import TempMailManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


async def main() -> None:
    proxy = str(settings.scraper.proxy) if settings.scraper.proxy else None
    graphql_client = IndeedGraphQLClient(proxy=proxy)
    mail_manager = TempMailManager()

    try:
        session_data = await get_session(
            graphql_client=graphql_client,
            mail_manager=mail_manager,
            force_login=settings.scraper.force_login,
            captcha_api_key=settings.captcha_api_key,
        )
        output = await scrape(
            session_data=session_data,
            graphql_client=graphql_client,
            query=settings.scraper.query,
            location=settings.scraper.location,
            limit=settings.scraper.limit,
        )
        print(f"Done. Results: {output}")
    finally:
        await graphql_client.close()


if __name__ == "__main__":
    asyncio.run(main())
