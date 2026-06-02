import os
import subprocess
import time
from pathlib import Path


def main():
    accounts_dir = Path("accounts")
    accounts_dir.mkdir(parents=True, exist_ok=True)

    target_count = 10

    while True:
        current_accounts = list(accounts_dir.glob("*.json"))
        current_count = len(current_accounts)
        print(f"\n--- Account Generation Progress: {current_count} / {target_count} ---")

        if current_count >= target_count:
            print("Successfully populated 10 accounts!")
            break

        print(f"Generating account #{current_count + 1}...")
        try:
            # Run the Scrapy spider with force_login=True to register a new account session
            subprocess.run(
                [
                    "uv",
                    "run",
                    "scrapy",
                    "crawl",
                    "indeed_basic",
                    "-a",
                    "force_login=True",
                    "-a",
                    "limit=1",
                ],
                check=True,
            )
            print(f"Account #{current_count + 1} generated successfully.")
        except subprocess.CalledProcessError as e:
            print(f"Failed to generate account: {e}")
            print("Retrying in 10 seconds...")
            time.sleep(10)


if __name__ == "__main__":
    main()
