from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "public" / "data.json"
TORONTO = ZoneInfo("America/Toronto")
UTC = ZoneInfo("UTC")
USER_AGENT = "ERWaitLive/1.0 (+https://barsnbolts.github.io/ticketmaster-helper/)"
TIMEOUT = 35

HOSPITALS = {
    "credit-valley": {"hospital_name": "Credit Valley Hospital", "city": "Mississauga", "source_name": "Trillium Health Partners", "source_url": "https://www.thp.ca/patientservices/emergencycare"},
    "milton-district": {"hospital_name": "Milton District Hospital", "city": "Milton", "source_name": "Halton Healthcare", "source_url": "https://www.haltonhealthcare.on.ca/emergency-department"},
    "oakville-trafalgar": {"hospital_name": "Oakville Trafalgar Memorial Hospital", "city": "Oakville", "source_name": "Halton Healthcare", "source_url": "https://www.haltonhealthcare.on.ca/emergency-department"},
}


def now() -> datetime:
    return datetime.now(TORONTO)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def payload_hash(value: Any) -> str:
    if not isinstance(value, (str, bytes)):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    if isinstance(value, str):
        value = value.encode("utf-8", "replace")
    return hashlib.sha256(value).hexdigest()


def parse_wait(text: str) -> int | None:
    text = normalize_space(text).lower()
    patterns = [
        r"(?P<h>\d+)\s*hour(?:\(s\)|s)?(?:\s*and)?\s*(?P<m>\d+)\s*minute(?:\(s\)|s)?",
        r"(?P<h>\d+)\s*h\s*(?P<m>\d+)\s*m",
        r"(?P<h>\d+(?:\.\d+)?)\s*hour(?:\(s\)|s)?",
        r"(?P<m>\d+)\s*minute(?:\(s\)|s)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        groups = match.groupdict()
        if groups.get("h") and "." in groups["h"]:
            return round(float(groups["h"]) * 60)
        return int(groups.get("h") or 0) * 60 + int(groups.get("m") or 0)
    return None


def parse_local_timestamp(text: str, reference: datetime | None = None) -> datetime | None:
    reference = reference or now()
    cleaned = normalize_space(text)
    cleaned = re.sub(r"^(?:last\s+updated\s*:|as\s+of\s+)", "", cleaned, flags=re.I)
    default = reference.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        parsed = date_parser.parse(cleaned, fuzzy=True, default=default)
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TORONTO)
    else:
        parsed = parsed.astimezone(TORONTO)
    if not re.search(r"\b20\d{2}\b", cleaned):
        parsed = parsed.replace(year=reference.year)
        if parsed - reference > timedelta(days=2):
            parsed = parsed.replace(year=reference.year - 1)
    return parsed


def request_text(url: str) -> str:
    response = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    response.raise_for_status()
    return response.text


def observation(hospital_id: str, **updates: Any) -> dict[str, Any]:
    meta = HOSPITALS[hospital_id]
    base = {
        "hospital_id": hospital_id,
        "hospital_name": meta["hospital_name"],
        "city": meta["city"],
        "wait_minutes": None,
        "patients_total": None,
        "patients_waiting": None,
        "source_name": meta["source_name"],
        "source_url": meta["source_url"],
        "source_updated_at": None,
        "retrieved_at": iso(now()),
        "source_tier": 1,
        "source_status": "current",
        "raw_payload_hash": None,
        "validation_flags": [],
    }
    base.update(updates)
    return base


def collect_halton() -> list[dict[str, Any]]:
    url = "https://www.haltonhealthcare.on.ca/emergency-department"
    html = request_text(url)
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    retrieved = now()
    stamp_match = re.search(r"Last\s+Updated\s*:\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+(?:\d{4}\s+)?\d{1,2}:\d{2}\s*(?:AM|PM))", text, re.I)
    source_updated = parse_local_timestamp(stamp_match.group(1), retrieved) if stamp_match else None
    section_re = re.compile(
        r"(?P<name>Milton\s+District\s+Hospital|Oakville\s+Trafalgar\s+Memorial\s+Hospital)\s+"
        r"(?P<wait>\d+\s+Hour\(s\)\s+and\s+\d+\s+Minute\(s\))\s+"
        r"All\s+Patients\s+in\s+Emergency\s+Department\s+(?P<total>\d+)\s+"
        r"(?:\*\s*)*Patients\s+Waiting\s+to\s+be\s+seen\s+(?P<waiting>\d+)", re.I)
    ids = {"milton district hospital": "milton-district", "oakville trafalgar memorial hospital": "oakville-trafalgar"}
    output: list[dict[str, Any]] = []
    for match in section_re.finditer(text):
        hospital_id = ids[normalize_space(match.group("name")).lower()]
        output.append(observation(hospital_id, wait_minutes=parse_wait(match.group("wait")), patients_total=int(match.group("total")), patients_waiting=int(match.group("waiting")), source_updated_at=iso(source_updated), retrieved_at=iso(retrieved), raw_payload_hash=payload_hash(html)))
    if len(output) != 2:
        output = []
        for hospital_id in ("milton-district", "oakville-trafalgar"):
            name = HOSPITALS[hospital_id]["hospital_name"]
            start = re.search(re.escape(name), text, re.I)
            if not start:
                continue
            tail = text[start.end():start.end() + 900]
            wait = parse_wait(tail)
            total_m = re.search(r"All\s+Patients\s+in\s+Emergency\s+Department\s+(\d+)", tail, re.I)
            waiting_m = re.search(r"Patients\s+Waiting\s+to\s+be\s+seen\s+(\d+)", tail, re.I)
            if wait is None:
                continue
            output.append(observation(hospital_id, wait_minutes=wait, patients_total=int(total_m.group(1)) if total_m else None, patients_waiting=int(waiting_m.group(1)) if waiting_m else None, source_updated_at=iso(source_updated), retrieved_at=iso(retrieved), raw_payload_hash=payload_hash(html), validation_flags=["layout_fallback_parser"]))
    if len(output) != 2:
        raise RuntimeError("Halton page did not yield both Milton and Oakville observations")
    return output


def parse_thp_rendered(text: str, raw: Any) -> dict[str, Any]:
    retrieved = now()
    normalized = normalize_space(text)
    section_m = re.search(r"Credit\s+Valley\s+Hospital\s*:\s*Emergency\s+Department\s+Wait\s+Times(?P<section>.*?)(?:Disclaimer|Emergency\s+Department\s+Wait\s+Time\s+Frequently|$)", normalized, re.I)
    if not section_m:
        raise RuntimeError("Rendered THP page did not contain the Credit Valley wait section")
    section = section_m.group("section")
    date_time = re.search(r"DATE\s*:\s*([^|]{3,40})\|\s*TIME\s*:\s*([^|]{2,20})", section, re.I)
    updated = parse_local_timestamp(" ".join(date_time.groups()), retrieved) if date_time else None
    wait_block = re.search(r"ESTIMATED\s+WAIT\s+TIME\s+TO\s+SEE\s+A\s+DOCTOR\s+(.*?)(?:Definition|TOTAL\s+PATIENTS)", section, re.I)
    wait = parse_wait(wait_block.group(1)) if wait_block else None
    total_m = re.search(r"TOTAL\s+PATIENTS\s+IN\s+THE\s+EMERGENCY\s+DEPARTMENT\s+(\d+)", section, re.I)
    total = int(total_m.group(1)) if total_m else None
    if updated and updated.year <= 2025 and wait in {None, 0} and total in {None, 0}:
        raise RuntimeError("Rejected THP January 23, 2025 zero-value placeholder")
    if wait is None:
        raise RuntimeError("THP rendered page did not contain a valid wait time")
    return observation("credit-valley", wait_minutes=wait, patients_total=total, source_updated_at=iso(updated), retrieved_at=iso(retrieved), raw_payload_hash=payload_hash(raw))


def parse_thp_json(payload: Any) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            serialized = json.dumps(value, ensure_ascii=False).lower()
            if any(token in serialized for token in ("credit valley", "creditvalley", '"cvh"')) and "wait" in serialized:
                candidates.append(value)
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    walk(payload)
    for item in sorted(candidates, key=lambda value: len(json.dumps(value)), reverse=True):
        flat: dict[str, Any] = {}
        def flatten(value: Any, path: tuple[str, ...] = ()) -> None:
            if isinstance(value, dict):
                for key, child in value.items(): flatten(child, path + (str(key).lower(),))
            elif isinstance(value, list):
                for index, child in enumerate(value): flatten(child, path + (str(index),))
            elif isinstance(value, (str, int, float)): flat[".".join(path)] = value
        flatten(item)
        def first(*terms: str) -> Any:
            matches = [(len(key), value) for key, value in flat.items() if all(term in key for term in terms)]
            return sorted(matches)[0][1] if matches else None
        wait_raw = first("wait", "minute") or first("estimated", "wait") or first("wait")
        if isinstance(wait_raw, (int, float)):
            wait = round(float(wait_raw) if float(wait_raw) > 20 else float(wait_raw) * 60)
        else:
            wait = parse_wait(str(wait_raw)) if wait_raw is not None else None
        if wait is None: continue
        total_raw = first("total", "patient")
        waiting_raw = first("waiting", "patient")
        updated_raw = first("updated") or first("timestamp")
        updated = parse_local_timestamp(str(updated_raw)) if updated_raw is not None else None
        if updated and updated.year <= 2025 and wait == 0 and not total_raw: continue
        return observation("credit-valley", wait_minutes=wait, patients_total=int(float(total_raw)) if total_raw is not None else None, patients_waiting=int(float(waiting_raw)) if waiting_raw is not None else None, source_updated_at=iso(updated), raw_payload_hash=payload_hash(payload))
    return None


async def collect_thp_browser() -> dict[str, Any]:
    from playwright.async_api import async_playwright
    json_payloads: list[Any] = []
    url = "https://www.thp.ca/emergency/A/visit.html"
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=USER_AGENT, viewport={"width": 1280, "height": 900})
        async def capture(response: Any) -> None:
            try:
                if "json" in (response.headers.get("content-type") or "").lower(): json_payloads.append(await response.json())
            except Exception: pass
        page.on("response", capture)
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(2500)
        body_text = await page.locator("body").inner_text()
        html = await page.content()
        await browser.close()
    for payload in reversed(json_payloads):
        parsed = parse_thp_json(payload)
        if parsed: return parsed
    return parse_thp_rendered(body_text, html)


def collect_thp() -> dict[str, Any]:
    try:
        html = request_text("https://www.thp.ca/emergency/A/visit.html")
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        return parse_thp_rendered(text, html)
    except Exception:
        return asyncio.run(collect_thp_browser())


def collect_erstat(hospital_id: str) -> dict[str, Any]:
    slugs = {"credit-valley": "credit-valley-hospital", "milton-district": "milton-district-hospital", "oakville-trafalgar": "oakville-trafalgar-memorial-hospital"}
    url = f"https://erstat.ca/hospitals/on/{slugs[hospital_id]}"
    html = request_text(url)
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    current = re.search(r"Current\s+ER\s+wait\s+([0-9hHmM\s]+)", text, re.I)
    wait = parse_wait(current.group(1)) if current else None
    if wait is None: raise RuntimeError("ERstat page did not contain a current wait")
    stamp = re.search(r"\b(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\b", text)
    updated = datetime.strptime(stamp.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).astimezone(TORONTO) if stamp else None
    waiting_m = re.search(r"(\d+)\s+waiting", text, re.I)
    treated_m = re.search(r"(\d+)\s+being\s+treated", text, re.I)
    total = int(waiting_m.group(1)) + int(treated_m.group(1)) if waiting_m and treated_m else None
    return observation(hospital_id, source_name="ERstat fallback", source_url=url, source_tier=3, wait_minutes=wait, patients_total=total, patients_waiting=int(waiting_m.group(1)) if waiting_m else None, source_updated_at=iso(updated), raw_payload_hash=payload_hash(html), validation_flags=["third_party_fallback"])


def source_age_minutes(obs: dict[str, Any]) -> float | None:
    raw = obs.get("source_updated_at") or obs.get("retrieved_at")
    if not raw: return None
    try: dt = datetime.fromisoformat(raw).astimezone(TORONTO)
    except ValueError: return None
    return max(0.0, (now() - dt).total_seconds() / 60)


def validate(obs: dict[str, Any]) -> dict[str, Any]:
    flags = list(obs.get("validation_flags") or [])
    wait = obs.get("wait_minutes")
    if wait is None:
        flags.append("missing_wait"); obs["source_status"] = "unavailable"
    elif not 0 <= int(wait) <= 1440:
        flags.append("implausible_wait"); obs["source_status"] = "unavailable"
    age = source_age_minutes(obs)
    tier = int(obs.get("source_tier") or 9)
    if age is None:
        flags.append("missing_source_timestamp"); obs["source_status"] = "delayed"
    else:
        warn, stale = ((5, 15) if obs["hospital_id"] == "credit-valley" and tier == 1 else (25, 45))
        if tier > 1: warn, stale = (20, 45)
        if age > stale:
            flags.append("stale_source"); obs["source_status"] = "stale"
        elif age > warn:
            flags.append("delayed_source"); obs["source_status"] = "delayed"
        else: obs["source_status"] = "current"
    obs["validation_flags"] = sorted(set(flags))
    return obs


def load_previous() -> dict[str, Any]:
    try: return json.loads(DATA_PATH.read_text())
    except Exception: return {"hospitals": [], "source_health": []}


def preserve_or_fallback(hospital_id: str, previous_by_id: dict[str, dict[str, Any]], error: Exception) -> dict[str, Any]:
    try:
        fallback = validate(collect_erstat(hospital_id))
        fallback["validation_flags"] = sorted(set(fallback.get("validation_flags", []) + [f"official_error:{type(error).__name__}"]))
        return fallback
    except Exception as fallback_error:
        previous = dict(previous_by_id.get(hospital_id) or observation(hospital_id))
        previous["source_status"] = "stale" if previous.get("wait_minutes") is not None else "unavailable"
        previous["validation_flags"] = sorted(set((previous.get("validation_flags") or []) + [f"official_error:{type(error).__name__}", f"fallback_error:{type(fallback_error).__name__}"]))
        return previous


def merge_history(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    history = list((previous or {}).get("history") or [])
    if current.get("wait_minutes") is not None:
        point = {"time": current.get("source_updated_at") or current.get("retrieved_at"), "wait_minutes": current["wait_minutes"]}
        if not history or history[-1] != point: history.append(point)
    cutoff = now() - timedelta(hours=24)
    cleaned = []
    for point in history:
        try:
            if datetime.fromisoformat(point["time"]).astimezone(TORONTO) >= cutoff: cleaned.append(point)
        except Exception: continue
    current["history"] = cleaned[-300:]
    return current


def main() -> None:
    previous_doc = load_previous()
    previous_by_id = {item["hospital_id"]: item for item in previous_doc.get("hospitals", [])}
    current: dict[str, dict[str, Any]] = {}
    health: list[dict[str, Any]] = []
    started = now()
    try:
        for obs in collect_halton(): current[obs["hospital_id"]] = validate(obs)
        health.append({"source": "Halton Healthcare", "status": "ok", "checked_at": iso(now()), "error": None})
    except Exception as error:
        health.append({"source": "Halton Healthcare", "status": "error", "checked_at": iso(now()), "error": str(error)[:400]})
        for hospital_id in ("milton-district", "oakville-trafalgar"): current[hospital_id] = preserve_or_fallback(hospital_id, previous_by_id, error)
    try:
        current["credit-valley"] = validate(collect_thp())
        health.append({"source": "Trillium Health Partners", "status": "ok", "checked_at": iso(now()), "error": None})
    except Exception as error:
        health.append({"source": "Trillium Health Partners", "status": "error", "checked_at": iso(now()), "error": str(error)[:400]})
        current["credit-valley"] = preserve_or_fallback("credit-valley", previous_by_id, error)
    ordered = [merge_history(current[hospital_id], previous_by_id.get(hospital_id)) for hospital_id in ("credit-valley", "milton-district", "oakville-trafalgar")]
    document = {"generated_at": iso(now()), "collection_started_at": iso(started), "collector_interval_minutes": 5, "hospitals": ordered, "source_health": health}
    DATA_PATH.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"generated_at": document["generated_at"], "health": health}, indent=2))


if __name__ == "__main__":
    main()
