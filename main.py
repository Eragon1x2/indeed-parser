import argparse
import asyncio
import logging
import os

from dotenv import load_dotenv

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
    parser = argparse.ArgumentParser(description="Indeed job scraper")
    parser.add_argument("--query", default="python")
    parser.add_argument("--location", default="Warszawa")
    parser.add_argument("--limit", default="all", help="Number of jobs or 'all'")
    parser.add_argument("--force-login", action="store_true")
    parser.add_argument("--proxy", default=None, help="http://user:pass@host:port")
    args = parser.parse_args()

    graphql_client = IndeedGraphQLClient(proxy=args.proxy)
    mail_manager = TempMailManager()

    try:
        session_data = await get_session(
            graphql_client=graphql_client,
            mail_manager=mail_manager,
            force_login=args.force_login,
            captcha_api_key=os.environ.get("CAPTCHA_API_KEY", ""),
        )
        output = await scrape(
            session_data=session_data,
            graphql_client=graphql_client,
            query=args.query,
            location=args.location,
            limit=args.limit,
        )
        print(f"Done. Results: {output}")
    finally:
        await graphql_client.close()


if __name__ == "__main__":
    asyncio.run(main())
