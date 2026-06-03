import asyncio
import logging
import os

from dotenv import load_dotenv

import config
from indeed_scraper.scraper import scrape
from indeed_scraper.session import get_session
from indeed_scraper.utils.graphql import IndeedGraphQLClient
from indeed_scraper.utils.temp_mail import TempMailManager

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


async def main() -> None:
    graphql_client = IndeedGraphQLClient(proxy=config.PROXY)
    mail_manager = TempMailManager()

    try:
        session_data = await get_session(
            graphql_client=graphql_client,
            mail_manager=mail_manager,
            force_login=config.FORCE_LOGIN,
            captcha_api_key=os.environ.get("CAPTCHA_API_KEY", ""),
        )
        output = await scrape(
            session_data=session_data,
            graphql_client=graphql_client,
            query=config.QUERY,
            location=config.LOCATION,
            limit=config.LIMIT,
        )
        print(f"Done. Results: {output}")
    finally:
        await graphql_client.close()


if __name__ == "__main__":
    asyncio.run(main())
