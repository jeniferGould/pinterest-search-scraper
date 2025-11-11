import csv
import json
import logging
import os
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger("exporters")

class DataExporter:
    """
    Export scraped data into various formats: JSON, JSONL, CSV, XLSX, XML.
    """

    def __init__(self, output_dir: str, default_format: str = "json") -> None:
        self.output_dir = output_dir
        self.default_format = default_format.lower()
        os.makedirs(self.output_dir, exist_ok=True)

    def _build_path(self, filename_stub: str, extension: str) -> str:
        safe_stub = "".join(
            c if c.isalnum() or c in ("-", "_") else "_" for c in filename_stub
        )
        return os.path.join(self.output_dir, f"{safe_stub}.{extension}")

    def export(
        self,
        data: Iterable[Dict[str, Any]],
        filename_stub: str,
        output_format: Optional[str] = None,
    ) -> str:
        data_list = list(data)
        fmt = (output_format or self.default_format).lower()

        if fmt not in {"json", "jsonl", "csv", "xlsx", "excel", "xml"}:
            raise ValueError(f"Unsupported output format: {fmt}")

        if fmt == "json":
            path = self._build_path(filename_stub, "json")
            self._export_json(data_list, path)
        elif fmt == "jsonl":
            path = self._build_path(filename_stub, "jsonl")
            self._export_jsonl(data_list, path)
        elif fmt == "csv":
            path = self._build_path(filename_stub, "csv")
            self._export_csv(data_list, path)
        elif fmt in {"xlsx", "excel"}:
            path = self._build_path(filename_stub, "xlsx")
            self._export_excel(data_list, path)
        else:  # xml
            path = self._build_path(filename_stub, "xml")
            self._export_xml(data_list, path)

        return path

    def _export_json(self, data: List[Dict[str, Any]], path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("JSON export written to %s", path)

    def _export_jsonl(self, data: List[Dict[str, Any]], path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        logger.info("JSONL export written to %s", path)

    def _export_csv(self, data: List[Dict[str, Any]], path: str) -> None:
        if not data:
            # Write an empty file with no headers
            open(path, "w", encoding="utf-8").close()
            logger.warning("CSV export created empty file at %s", path)
            return

        # Collect fieldnames from union of keys across all rows (flattening nested dicts)
        flat_rows = [self._flatten_dict(row) for row in data]
        fieldnames: List[str] = []
        for row in flat_rows:
            for key in row.keys():
                if key not in fieldnames:
                    fieldnames.append(key)

        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(flat_rows)

        logger.info("CSV export written to %s", path)

    def _export_excel(self, data: List[Dict[str, Any]], path: str) -> None:
        df = pd.DataFrame([self._flatten_dict(row) for row in data])
        df.to_excel(path, index=False)
        logger.info("Excel export written to %s", path)

    def _export_xml(self, data: List[Dict[str, Any]], path: str) -> None:
        from xml.etree.ElementTree import Element, SubElement, ElementTree

        root = Element("pins")

        for row in data:
            pin_el = SubElement(root, "pin")
            flat = self._flatten_dict(row)
            for key, value in flat.items():
                child = SubElement(pin_el, key)
                child.text = "" if value is None else str(value)

        tree = ElementTree(root)
        tree.write(path, encoding="utf-8", xml_declaration=True)
        logger.info("XML export written to %s", path)

    def _flatten_dict(
        self, data: Dict[str, Any], parent_key: str = "", sep: str = "."
    ) -> Dict[str, Any]:
        """
        Flatten nested dictionaries using dot-separated keys.
        """
        items: Dict[str, Any] = {}
        for key, value in data.items():
            new_key = f"{parent_key}{sep}{key}" if parent_key else key
            if isinstance(value, dict):
                items.update(self._flatten_dict(value, new_key, sep=sep))
            else:
                items[new_key] = value
        return items