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


class IndeedParserPipeline:
    def process_item(self, item: dict, spider: object) -> dict:
        details_raw = item.get("details_raw")
        if not details_raw:
            return item

        viewjob = (details_raw.get("data") or {}).get("viewjob") or {}
        detail_job = viewjob.get("job") or {}
        detail_info = viewjob.get("details") or {}
        comp = detail_job.get("compensation") or {}
        salary_range = (comp.get("baseSalary") or {}).get("range") or {}
        apply_method = detail_info.get("applyMethod") or {}

        item["description"] = (detail_job.get("description") or {}).get("text")
        item["date_published"] = detail_job.get("datePublished")
        item["salary"] = comp.get("formattedText")
        item["salary_min"] = salary_range.get("min")
        item["salary_max"] = salary_range.get("max")
        item["remote"] = (detail_info.get("remoteWorkModel") or {}).get("text")
        item["job_types"] = (detail_info.get("jobTypeAndShiftSchedule") or {}).get(
            "jobTypes"
        ) or []
        item["benefits"] = [
            b.get("label") for b in (detail_info.get("benefit") or {}).get("benefits") or []
        ]
        item["apply_url"] = apply_method.get("applyUrl") or apply_method.get("continueUrl")
        item["address"] = (detail_info.get("location") or {}).get("formattedStreetAddress")

        # Clean up details_raw to keep output file clean
        item.pop("details_raw", None)
        return item
