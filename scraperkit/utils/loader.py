import importlib
import inspect
import pkgutil

import scrapy


def load_spiders(package_name: str) -> dict[str, type[scrapy.Spider]]:
    spiders: dict[str, type[scrapy.Spider]] = {}
    package = importlib.import_module(package_name)
    for _, module_name, _ in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        module = importlib.import_module(module_name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, scrapy.Spider)
                and obj is not scrapy.Spider
                and getattr(obj, "name", None)
            ):
                spiders[obj.name] = obj
    return spiders
