"""Crawl NVIDIA (and peer) filings data from SEC EDGAR and store it under data/raw/.

SEC fair-access policy requires a declared User-Agent with a contact e-mail:
    https://www.sec.gov/os/accessing-edgar-data
Set it with the SEC_USER_AGENT environment variable, e.g.
    SEC_USER_AGENT="Your Name your@email.com" python scripts/fetch_sec.py
"""
import gzip
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

NVIDIA_CIK = "0001045810"

# SEC filers among NVIDIA's global peers (non-SEC filers such as Samsung / SK hynix are static).
PEER_CIKS = {
    "AMD": "0000002488",
    "INTC": "0000050863",
    "AVGO": "0001730168",
    "QCOM": "0000804328",
    "MU": "0000723125",
    "MRVL": "0001835632",
    "TSM": "0001046179",
    "ARM": "0001973239",
}

PEER_CONCEPTS = {
    # US GAAP
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
    "NetIncomeLoss", "OperatingIncomeLoss", "GrossProfit", "ResearchAndDevelopmentExpense",
    "Assets", "StockholdersEquity",
    # IFRS (20-F filers such as TSMC)
    "Revenue", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent", "ProfitLossFromOperatingActivities",
    "GrossProfit", "ResearchAndDevelopmentExpense", "Equity", "EquityAttributableToOwnersOfParent",
}


def get_json(url: str, ua: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    time.sleep(0.2)  # stay well under SEC's 10 requests/second limit
    return json.loads(body)


def save(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
    print(f"saved {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.1f} MB)")


def main() -> int:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if "@" not in ua:
        print("SEC_USER_AGENT must contain a contact e-mail (SEC policy).", file=sys.stderr)
        return 1

    save(get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{NVIDIA_CIK}.json", ua),
         RAW / "nvidia_companyfacts.json")

    # Filing index: keep only the columns we use, to keep the repo small.
    sub = get_json(f"https://data.sec.gov/submissions/CIK{NVIDIA_CIK}.json", ua)
    recent = sub["filings"]["recent"]
    keep = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")
    filings = [dict(zip(keep, row)) for row in zip(*(recent[k] for k in keep))]
    save({"name": sub["name"], "cik": sub["cik"], "tickers": sub.get("tickers"),
          "exchanges": sub.get("exchanges"), "sic": sub.get("sic"),
          "sicDescription": sub.get("sicDescription"), "fiscalYearEnd": sub.get("fiscalYearEnd"),
          "filings": filings}, RAW / "nvidia_submissions.json")

    for ticker, cik in PEER_CIKS.items():
        try:
            facts = get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", ua)
        except Exception as exc:  # a single peer failing should not stop the update
            print(f"warning: {ticker} skipped ({exc})", file=sys.stderr)
            continue
        # Peers only need a handful of headline concepts; trim to keep the repo light.
        slim = {tax: {c: v for c, v in concepts.items() if c in PEER_CONCEPTS}
                for tax, concepts in facts.get("facts", {}).items()}
        save({"cik": facts.get("cik"), "entityName": facts.get("entityName"), "facts": slim},
             RAW / "peers" / f"{ticker}_companyfacts.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
