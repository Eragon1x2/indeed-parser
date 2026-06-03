from scrapy.exceptions import DropItem


class DuplicatesPipeline:
    def __init__(self) -> None:
        self.seen_keys: set[str] = set()

    def process_item(self, item: dict, spider: object) -> dict:
        job_key = item.get("job_key")
        if not job_key:
            raise DropItem("Missing job_key")
        if job_key in self.seen_keys:
            raise DropItem(f"Duplicate: {job_key}")
        self.seen_keys.add(job_key)
        return item
