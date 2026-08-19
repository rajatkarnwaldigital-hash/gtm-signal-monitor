"""Shared client for Getro-hosted job boards.

Getro powers a large share of VC and accelerator portfolio boards, so more than
one source here talks to the same API. Everything platform-specific lives in
this module; a source only supplies its network id and its filters.

Three Getro behaviours that are silent-failure traps, learned the hard way:

  * The API returns **406 without an explicit `Accept: application/json`**.
    This previously read as a 401 and led to a whole endpoint being written off.
  * `hitsPerPage` is **capped at 20** server-side. Larger values are silently
    clamped, so pagination is mandatory rather than optional.
  * Only some filter keys actually filter. `job_functions` and
    `searchable_locations` work. `stage`, `locations`, `countries` and
    `location_details` are **accepted and then ignored**, returning the full
    unfiltered set. Anything in that second group must be applied locally.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

API = "https://api.getro.com/api/v2/collections/{net}/search/jobs"
ORG_API = "https://api.getro.com/api/v2/collections/{net}/organizations/{slug}"

PAGE_SIZE = 20   # server-side cap; larger values are silently clamped
MAX_PAGES = 200  # safety rail if `count` ever misbehaves
REQUEST_PAUSE = 0.25
TIMEOUT = 30

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Getro encodes headcount as a bucket ordinal, not a real employee count.
HEADCOUNT_BUCKETS = {
    1: "1-10", 2: "11-50", 3: "51-200", 4: "201-500",
    5: "501-1000", 6: "1001-5000", 7: "5000+",
}

# Boards render status badges adjacent to the title, and they can arrive
# concatenated onto it with no separator ("...(Vet Sales)NewOn-Site").
_BADGE_RE = re.compile(r"(?:New|Hybrid|On-?Site|Remote|Urgent|Featured)+$")


def clean_title(title: str) -> str:
    title = " ".join((title or "").split())
    prev = None
    while prev != title:
        prev = title
        title = _BADGE_RE.sub("", title).strip()
    return title


def _headers(board_url: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",  # without this the API 406s
        "Origin": board_url,
        "Referer": board_url + "/",
        "User-Agent": UA,
    }


def post(net: int, payload: dict, board_url: str, attempts: int = 4) -> dict:
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                API.format(net=net), data=json.dumps(payload).encode(),
                headers=_headers(board_url))
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500 and e.code != 429:
                raise
        except Exception as e:  # noqa: BLE001 - retry any transport fault
            last = e
        if i < attempts - 1:
            time.sleep(2 ** i)
    raise RuntimeError(f"Getro request failed after {attempts} attempts: {last}")


def results(body: dict, key: str = "jobs") -> tuple[list, int]:
    """Unwrap the response and fail loudly if its shape moved."""
    res = (body or {}).get("results")
    if not isinstance(res, dict) or key not in res or "count" not in res:
        raise RuntimeError(
            f"Getro response shape changed — expected results.{key} and results.count, "
            f"got {list(res) if isinstance(res, dict) else type(res).__name__}")
    return res[key], res["count"]


def count(net: int, board_url: str, filters: dict) -> int:
    return results(post(net, {"hitsPerPage": 0, "page": 0, "filters": filters}, board_url))[1]


def walk(net: int, board_url: str, filters: dict) -> list[dict]:
    """Page through every job matching `filters`."""
    jobs: list[dict] = []
    for _ in range(MAX_PAGES):
        batch, total = results(post(
            net, {"hitsPerPage": PAGE_SIZE, "page": len(jobs) // PAGE_SIZE,
                  "filters": filters}, board_url))
        jobs.extend(batch)
        if len(batch) < PAGE_SIZE or len(jobs) >= total:
            break
        time.sleep(REQUEST_PAUSE)
    return jobs


def org(net: int, slug: str, board_url: str) -> dict:
    """One company profile. Best-effort: enrichment must never break a run."""
    try:
        req = urllib.request.Request(ORG_API.format(net=net, slug=slug),
                                     headers=_headers(board_url))
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return (json.loads(resp.read().decode()).get("data") or {}).get("attributes") or {}
    except Exception:  # noqa: BLE001
        return {}


def validate(net: int, board_url: str, functions: list[str]) -> None:
    """Catch silent breakage before it looks like a quiet day of no postings.

    A renamed job_functions value matches nothing and emits zero leads without
    erroring, which is indistinguishable from a genuinely quiet run.
    """
    unfiltered = count(net, board_url, {})
    if not unfiltered:
        return
    filtered = count(net, board_url, {"job_functions": functions})
    if filtered == 0:
        raise RuntimeError(
            f"job_functions {functions} matched 0 of {unfiltered} jobs on network "
            f"{net} — Getro's vocabulary likely changed; re-derive it.")
    if filtered == unfiltered:
        raise RuntimeError(
            f"job_functions returned the unfiltered count on network {net} — "
            "server-side filtering is no longer applied.")
