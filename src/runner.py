import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from .extractors.pinterest_parser import PinterestSearchScraper
from .outputs.exporters import DataExporter

def configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

def load_json_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_config(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    cfg = load_json_file(path)
    if not isinstance(cfg, dict):
        raise ValueError("Config file must contain a JSON object at top level.")
    return cfg

def load_jobs_from_inputs(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Inputs file not found: {path}")
    data = load_json_file(path)
    if not isinstance(data, list):
        raise ValueError("Inputs file must contain a JSON array of job objects.")
    jobs: List[Dict[str, Any]] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            logging.warning("Skipping non-object job at index %d", idx)
            continue
        if "query" not in item:
            logging.warning("Skipping job at index %d missing 'query' field", idx)
            continue
        jobs.append(item)
    return jobs

def build_jobs(
    args: argparse.Namespace,
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    default_limit = int(config.get("default_limit", 50))
    default_filter = str(config.get("content_filter", "all"))

    if args.query:
        job = {
            "query": args.query,
            "limit": args.limit or default_limit,
            "filter": args.filter or default_filter,
        }
        return [job]

    inputs_path = args.inputs
    jobs = load_jobs_from_inputs(inputs_path)
    for job in jobs:
        job.setdefault("limit", default_limit)
        job.setdefault("filter", default_filter)
    return jobs

def create_scraper(config: Dict[str, Any]) -> PinterestSearchScraper:
    http_cfg = config.get("http", {}) or {}
    base_url = config.get("base_url", "https://www.pinterest.com/search/pins/")
    timeout = float(http_cfg.get("timeout", 10))
    max_retries = int(http_cfg.get("max_retries", 3))
    backoff_factor = float(http_cfg.get("backoff_factor", 0.5))
    user_agent = str(
        http_cfg.get(
            "user_agent",
            "PinterestSearchScraper/1.0 (https://bitbash.dev)",
        )
    )

    return PinterestSearchScraper(
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
        backoff_factor=backoff_factor,
        user_agent=user_agent,
    )

def ensure_directory(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pinterest Search Scraper - run search jobs and export results."
    )
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_config = os.path.join(default_root, "src", "config", "settings.example.json")
    default_inputs = os.path.join(default_root, "data", "inputs.sample.json")
    default_output_dir = os.path.join(default_root, "data")

    parser.add_argument(
        "--config",
        type=str,
        default=default_config,
        help="Path to configuration JSON file.",
    )
    parser.add_argument(
        "--inputs",
        type=str,
        default=default_inputs,
        help="Path to JSON file containing input jobs.",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Pinterest search query. If provided, overrides the inputs file.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of pins to scrape per query.",
    )
    parser.add_argument(
        "--filter",
        type=str,
        default=None,
        choices=["all", "videos"],
        help="Content filter to apply to the search.",
    )
    parser.add_argument(
        "--output-format",
        type=str,
        default=None,
        choices=["json", "jsonl", "csv", "xlsx", "excel", "xml"],
        help="Output format for results.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=default_output_dir,
        help="Directory where exports will be written.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity (can be used multiple times).",
    )

    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    logger = logging.getLogger("runner")
    logger.debug("Starting runner with args: %s", args)

    try:
        config = load_config(args.config)
    except Exception as exc:
        logger.error("Failed to load config: %s", exc)
        return 1

    try:
        jobs = build_jobs(args, config)
    except Exception as exc:
        logger.error("Failed to load jobs: %s", exc)
        return 1

    if not jobs:
        logger.error("No valid jobs to run. Exiting.")
        return 1

    scraper = create_scraper(config)
    output_cfg = config.get("output", {}) or {}
    output_dir = args.output_dir or output_cfg.get("directory") or default_output_dir
    output_format = (args.output_format or output_cfg.get("format") or "json").lower()
    ensure_directory(output_dir)
    exporter = DataExporter(output_dir=output_dir, default_format=output_format)

    all_results: List[Dict[str, Any]] = []
    for idx, job in enumerate(jobs, start=1):
        query = job["query"]
        limit = int(job.get("limit", config.get("default_limit", 50)))
        content_filter = str(job.get("filter", config.get("content_filter", "all")))

        logger.info(
            "Running job %d/%d: query=%r, limit=%d, filter=%s",
            idx,
            len(jobs),
            query,
            limit,
            content_filter,
        )

        try:
            results = scraper.search(
                query=query,
                limit=limit,
                content_filter=content_filter,
            )
            logger.info(
                "Job %d completed: retrieved %d pins for query %r",
                idx,
                len(results),
                query,
            )
            all_results.extend(results)
        except Exception as exc:
            logger.exception(
                "Job %d failed for query %r: %s", idx, query, exc
            )

    if not all_results:
        logger.warning("No results collected from any job.")
    else:
        logger.info("Total pins collected across all jobs: %d", len(all_results))

    try:
        output_path = exporter.export(
            data=all_results,
            filename_stub="pinterest_results",
            output_format=output_format,
        )
        logger.info("Exported results to %s", output_path)
    except Exception as exc:
        logger.exception("Failed to export results: %s", exc)
        return 1

    return 0

if __name__ == "__main__":
    raise SystemExit(main())