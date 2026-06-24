import csv
import json
import re
from pathlib import Path

import pdfplumber


PDF_PATH = Path("第七版中国计算机学会推荐国际学术会议和期刊目录（CCF 2026）.pdf")
OUT_DIR = Path("outputs/ccf2026")
JSON_PATH = OUT_DIR / "ccf2026_entries.json"
CSV_PATH = OUT_DIR / "ccf2026_entries.csv"

RANK_RE = re.compile(r"[一二三]、\s*([ABC])\s*类")
FIELD_RE = re.compile(r"（([^（）]+)）")


def clean_cell(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def field_from_text(text):
    header_lines = [line.strip() for line in text.splitlines()[:5]]
    for line in header_lines:
        if not (line.startswith("（") and line.endswith("）")):
            continue
        match = FIELD_RE.fullmatch(line)
        if not match:
            continue
        value = match.group(1).strip()
        if value and not re.fullmatch(r"\d{4}\s*年?", value):
            return value
    return None


def item_type_from_header(header):
    joined = " ".join(clean_cell(c) for c in header)
    if "会议简称" in joined or "会议全称" in joined:
        return "会议"
    if "期刊简称" in joined or "期刊全称" in joined:
        return "期刊"
    return None


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    current_type = None
    current_field = None
    current_rank = None

    with pdfplumber.open(PDF_PATH) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""

            if "推荐国际学术期刊" in text:
                current_type = "期刊"
            elif "推荐国际学术会议" in text:
                current_type = "会议"

            field = field_from_text(text)
            if field:
                current_field = field

            rank = RANK_RE.search(text)
            if rank:
                current_rank = rank.group(1)

            for table in page.find_tables():
                rows = table.extract(x_tolerance=2, y_tolerance=3)
                if not rows:
                    continue

                header = [clean_cell(c) for c in rows[0]]
                table_type = item_type_from_header(header) or current_type
                if len(header) < 5 or "序号" not in header[0]:
                    continue

                for raw_row in rows[1:]:
                    row = [clean_cell(c) for c in raw_row]
                    if len(row) < 5:
                        continue
                    number, abbr, full_name, publisher, url = row[:5]
                    if not number and not abbr and not full_name:
                        continue
                    entries.append(
                        {
                            "类型": table_type or "",
                            "领域": current_field or "",
                            "等级": current_rank or "",
                            "序号": number,
                            "简称": abbr,
                            "全称": full_name,
                            "出版社": publisher,
                            "网址": url,
                            "页码": page_index,
                        }
                    )

    with JSON_PATH.open("w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["类型", "领域", "等级", "序号", "简称", "全称", "出版社", "网址", "页码"],
        )
        writer.writeheader()
        writer.writerows(entries)

    print(f"entries={len(entries)}")
    print(JSON_PATH)
    print(CSV_PATH)


if __name__ == "__main__":
    main()
