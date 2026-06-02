from typing import Any

from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem


class DuplicatesPipeline:
    def __init__(self) -> None:
        self.seen_keys: set[str] = set()

    def process_item(self, item: Any, spider: Any) -> Any:
        adapter = ItemAdapter(item)

        job_key = adapter.get("job_key")

        if not job_key:
            raise DropItem("Missing job_key")

        if job_key in self.seen_keys:
            raise DropItem(f"Duplicate job: {job_key}")

        self.seen_keys.add(job_key)

        return item
