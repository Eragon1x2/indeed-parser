def build_job_item(meta: dict, details_raw: dict) -> dict:
    item: dict = {
        "job_key": meta["job_key"],
        "title": meta["title"],
        "company": meta["company"],
        "location": meta["location"],
        "search_data": meta["search_data"],
    }
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
    return item
