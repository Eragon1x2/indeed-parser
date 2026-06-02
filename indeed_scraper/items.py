import scrapy


class IndeedJobItem(scrapy.Item):
    job_key = scrapy.Field()
    title = scrapy.Field()
    company = scrapy.Field()
    location = scrapy.Field()
    search_data = scrapy.Field()
    description = scrapy.Field()
    date_published = scrapy.Field()
    salary = scrapy.Field()
    salary_min = scrapy.Field()
    salary_max = scrapy.Field()
    remote = scrapy.Field()
    job_types = scrapy.Field()
    benefits = scrapy.Field()
    apply_url = scrapy.Field()
    is_remote = scrapy.Field()
    address = scrapy.Field()
