from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

TORONTO = ZoneInfo("America/Toronto")
UA = "ER-Intel-Forkable/2.0 (+https://github.com/barsnbolts/ticketmaster-helper)"
TIMEOUT = 25
PARSER_VERSION = "forkable-2.0"
THP_API = "https://edwt-prd.thp.ca/waittimes/stats/CVH"
THP_PAGE = "https://www.thp.ca/emergency/A/visit.html"
HALTON_PAGE = "https://www.haltonhealthcare.on.ca/emergency-department"


def now_iso() -> str:
    return datetime.now(TORONTO).isoformat()


def sha(value: Any) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def get(url: str, accept: str = "application/json,text/html;q=0.9,*/*;q=0.8") -> tuple[requests.Response, int]:
    started = time.perf_counter()
    response = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA, "Accept": accept})
    elapsed = round((time.perf_counter() - started) * 1000)
    response.raise_for_status()
    return response, elapsed


def parse_stamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = date_parser.parse(value, fuzzy=True)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TORONTO)
        return parsed.astimezone(TORONTO).isoformat()
    except Exception:
        return None


def base(hospital_id: str, source_url: str) -> dict[str, Any]:
    return {
        "hospital_id": hospital_id,
        "wait_minutes": None,
        "total_patients": None,
        "waiting_patients": None,
        "source_reported_at": None,
        "source_time_label": None,
        "retrieved_at": now_iso(),
        "source_tier": "official",
        "source_url": source_url,
        "is_valid": False,
        "invalid_reason": None,
        "http_status": None,
        "response_ms": None,
        "parser_version": PARSER_VERSION,
        "payload_hash": None,
        "validation_flags": [],
    }


def validate(row: dict[str, Any]) -> dict[str, Any]:
    flags = list(row.get("validation_flags") or [])
    wait = row.get("wait_minutes")
    total = row.get("total_patients")
    waiting = row.get("waiting_patients")
    if wait is None:
        flags.append("missing_wait")
    elif not 0 <= int(wait) <= 1440:
        flags.append("implausible_wait")
    if total is not None and not 0 <= int(total) <= 1000:
        flags.append("implausible_total_patients")
    if waiting is not None and not 0 <= int(waiting) <= 1000:
        flags.append("implausible_waiting_patients")
    if total is not None and waiting is not None and waiting > total:
        flags.append("waiting_exceeds_total")
    row["validation_flags"] = sorted(set(flags))
    row["is_valid"] = not any(x in flags for x in ("missing_wait", "implausible_wait", "implausible_total_patients", "implausible_waiting_patients", "waiting_exceeds_total"))
    if not row["is_valid"] and not row.get("invalid_reason"):
        row["invalid_reason"] = ", ".join(row["validation_flags"])
    return row


def collect_thp() -> dict[str, Any]:
    row = base("cvh", THP_PAGE)
    try:
        response, elapsed = get(THP_API)
        payload = response.json()
        hours = payload.get("averageTimeToSeeDoctor80th")
        updated = payload.get("lastUpdated")
        row.update(
            wait_minutes=round(float(hours) * 60) if hours is not None else None,
            total_patients=payload.get("activePatients"),
            waiting_patients=payload.get("patientsWaitingToSeeDoctor"),
            source_reported_at=parse_stamp(updated),
            source_time_label=updated,
            http_status=response.status_code,
            response_ms=elapsed,
            payload_hash=sha(payload),
        )
        if (row["source_reported_at"] or "").startswith("2025-01-23") or ((hours in (None, 0)) and not payload.get("activePatients")):
            row["validation_flags"].append("known_thp_placeholder")
            row["invalid_reason"] = "Official THP feed returned its known placeholder values"
        return validate(row)
    except Exception as exc:
        row["invalid_reason"] = f"THP official feed failed: {type(exc).__name__}: {str(exc)[:220]}"
        row["validation_flags"] = ["source_request_failed"]
        return row


def parse_halton_section(text: str, heading: str) -> tuple[int | None, int | None, int | None]:
    start = text.lower().find(heading.lower())
    if start < 0:
        return None, None, None
    chunk = text[start : start + 650]
    wait = re.search(r"(\d{1,2})\s*Hour\(s\)\s*and\s*(\d{1,2})\s*Minute\(s\)", chunk, re.I)
    total = re.search(r"All\s+Patients\s+in\s+Emergency\s+Department\s*(\d{1,3})", chunk, re.I)
    waiting = re.search(r"Patients\s+Waiting\s+to\s+be\s+seen\s*(\d{1,3})", chunk, re.I)
    return (
        int(wait.group(1)) * 60 + int(wait.group(2)) if wait else None,
        int(total.group(1)) if total else None,
        int(waiting.group(1)) if waiting else None,
    )


def collect_halton() -> list[dict[str, Any]]:
    rows = [base("milton", HALTON_PAGE), base("otmh", HALTON_PAGE)]
    try:
        response, elapsed = get(HALTON_PAGE, "text/html,*/*;q=0.8")
        html = response.text
        text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        stamp_match = re.search(r"Last\s+Updated\s*:\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+(?:\d{4}\s+)?\d{1,2}:\d{2}\s*(?:AM|PM))", text, re.I)
        label = stamp_match.group(1) if stamp_match else None
        reported = parse_stamp(label)
        digest = sha(html.encode())
        targets = [("milton", "Milton District Hospital"), ("otmh", "Oakville Trafalgar Memorial Hospital")]
        output = []
        for hospital_id, heading in targets:
            wait, total, waiting = parse_halton_section(text, heading)
            row = base(hospital_id, HALTON_PAGE)
            row.update(
                wait_minutes=wait,
                total_patients=total,
                waiting_patients=waiting,
                source_reported_at=reported,
                source_time_label=label,
                http_status=response.status_code,
                response_ms=elapsed,
                payload_hash=digest,
            )
            output.append(validate(row))
        return output
    except Exception as exc:
        for row in rows:
            row["invalid_reason"] = f"Halton official page failed: {type(exc).__name__}: {str(exc)[:220]}"
            row["validation_flags"] = ["source_request_failed"]
        return rows


def post_row(row: dict[str, Any]) -> tuple[bool, str]:
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/ed_wait_snapshots"
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    response = requests.post(
        url,
        timeout=TIMEOUT,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        json=row,
    )
    if response.status_code in (200, 201, 204):
        return True, "inserted"
    if response.status_code == 409:
        return True, "duplicate"
    return False, f"HTTP {response.status_code}: {response.text[:300]}"


def main() -> None:
    missing = [name for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY") if not os.getenv(name)]
    if missing:
        raise SystemExit("Missing required environment variables: " + ", ".join(missing))
    rows = [collect_thp(), *collect_halton()]
    results = []
    failed = False
    for row in rows:
        ok, detail = post_row(row)
        failed = failed or not ok
        results.append({"hospital_id": row["hospital_id"], "valid": row["is_valid"], "stored": ok, "detail": detail})
    print(json.dumps({"retrieved_at": now_iso(), "results": results}, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
