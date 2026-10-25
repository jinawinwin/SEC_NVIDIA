"""Turn the raw SEC XBRL company facts into annual / half-year / quarterly datasets,
peer comparisons and the JSON that powers the dashboard (docs/).

Only the Python standard library is used so the monthly GitHub Action needs no installs.
"""
import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
DOCS_DATA = ROOT / "docs" / "data"

# NVIDIA stock splits after which earlier per-share figures must be restated.
SPLITS = [(date(2021, 7, 20), 4), (date(2024, 6, 10), 10)]

# metric key -> (label, unit, kind, candidate us-gaap concepts in priority order)
# kind: "flow" = duration (summable), "instant" = balance sheet, "per_share" = non-summable duration
METRICS = {
    "revenue": ("Revenue", "usd", "flow", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]),
    "cost_of_revenue": ("Cost of revenue", "usd", "flow", ["CostOfRevenue"]),
    "gross_profit": ("Gross profit", "usd", "flow", ["GrossProfit"]),
    "rnd": ("R&D expense", "usd", "flow", ["ResearchAndDevelopmentExpense"]),
    "sga": ("SG&A expense", "usd", "flow", ["SellingGeneralAndAdministrativeExpense"]),
    "opex": ("Operating expenses", "usd", "flow", ["OperatingExpenses"]),
    "operating_income": ("Operating income", "usd", "flow", ["OperatingIncomeLoss"]),
    "income_tax": ("Income tax", "usd", "flow", ["IncomeTaxExpenseBenefit"]),
    "net_income": ("Net income", "usd", "flow", ["NetIncomeLoss"]),
    "eps_diluted": ("Diluted EPS (split-adj.)", "usd_ps", "per_share", ["EarningsPerShareDiluted"]),
    "diluted_shares": ("Diluted shares (split-adj.)", "shares", "per_share", ["WeightedAverageNumberOfDilutedSharesOutstanding"]),
    "cfo": ("Operating cash flow", "usd", "flow", ["NetCashProvidedByUsedInOperatingActivities"]),
    "capex": ("Capital expenditures", "usd", "flow", ["PaymentsToAcquireProductiveAssets", "PaymentsToAcquirePropertyPlantAndEquipment"]),
    "buybacks": ("Share repurchases", "usd", "flow", ["PaymentsForRepurchaseOfCommonStock"]),
    "dividends": ("Dividends paid", "usd", "flow", ["PaymentsOfDividends"]),
    "sbc": ("Stock-based compensation", "usd", "flow", ["ShareBasedCompensation"]),
    "cash": ("Cash & equivalents", "usd", "instant", ["CashAndCashEquivalentsAtCarryingValue"]),
    "marketable_securities": ("Marketable securities", "usd", "instant", ["MarketableSecuritiesCurrent", "DebtSecuritiesCurrent"]),
    "receivables": ("Accounts receivable", "usd", "instant", ["AccountsReceivableNetCurrent"]),
    "inventory": ("Inventory", "usd", "instant", ["InventoryNet"]),
    "current_assets": ("Current assets", "usd", "instant", ["AssetsCurrent"]),
    "total_assets": ("Total assets", "usd", "instant", ["Assets"]),
    "current_liabilities": ("Current liabilities", "usd", "instant", ["LiabilitiesCurrent"]),
    "total_liabilities": ("Total liabilities", "usd", "instant", ["Liabilities"]),
    "long_term_debt": ("Total debt", "usd", "instant", ["LongTermDebt", "LongTermDebtNoncurrent"]),
    "equity": ("Shareholders' equity", "usd", "instant", ["StockholdersEquity"]),
}

DERIVED = {
    "fcf": ("Free cash flow", "usd"),
    "gross_margin": ("Gross margin", "pct"),
    "operating_margin": ("Operating margin", "pct"),
    "net_margin": ("Net margin", "pct"),
    "rnd_ratio": ("R&D / revenue", "pct"),
    "fcf_margin": ("FCF margin", "pct"),
    "tax_rate": ("Effective tax rate", "pct"),
    "current_ratio": ("Current ratio", "x"),
    "debt_to_equity": ("Debt / equity", "x"),
    "revenue_yoy": ("Revenue YoY", "pct"),
    "net_income_yoy": ("Net income YoY", "pct"),
    "eps_yoy": ("EPS YoY", "pct"),
    "revenue_qoq": ("Revenue QoQ", "pct"),
    "roe": ("ROE", "pct"),
    "roa": ("ROA", "pct"),
}

# Order of rows in the dashboard / Excel tables, grouped by statement.
TABLE_LAYOUT = [
    ("Income statement", ["revenue", "revenue_yoy", "revenue_qoq", "cost_of_revenue", "gross_profit", "gross_margin",
                          "rnd", "rnd_ratio", "sga", "opex", "operating_income", "operating_margin",
                          "income_tax", "tax_rate", "net_income", "net_income_yoy", "net_margin",
                          "eps_diluted", "eps_yoy", "diluted_shares"]),
    ("Cash flow", ["cfo", "capex", "fcf", "fcf_margin", "buybacks", "dividends", "sbc"]),
    ("Balance sheet", ["cash", "marketable_securities", "receivables", "inventory", "current_assets", "total_assets",
                       "current_liabilities", "total_liabilities", "long_term_debt", "equity",
                       "current_ratio", "debt_to_equity", "roe", "roa"]),
]

PEERS = [
    # ticker, name, country, listing, relationship to NVIDIA, SEC filer?
    ("NVDA", "NVIDIA", "United States", "NASDAQ: NVDA", "Subject company — GPUs, AI accelerators, networking, CUDA", True),
    ("AMD", "Advanced Micro Devices", "United States", "NASDAQ: AMD", "Direct rival — Instinct AI GPUs, Radeon gaming GPUs, EPYC CPUs", True),
    ("INTC", "Intel", "United States", "NASDAQ: INTC", "Rival — Gaudi AI accelerators, Arc GPUs, x86 CPUs, foundry", True),
    ("AVGO", "Broadcom", "United States", "NASDAQ: AVGO", "Rival — custom AI ASICs (XPUs) & Ethernet AI networking", True),
    ("MRVL", "Marvell Technology", "United States", "NASDAQ: MRVL", "Rival — custom AI silicon, optical/electrical interconnect", True),
    ("QCOM", "Qualcomm", "United States", "NASDAQ: QCOM", "Rival — edge/on-device AI SoCs, data-center inference", True),
    ("MU", "Micron Technology", "United States", "NASDAQ: MU", "Supplier — HBM memory for AI GPUs", True),
    ("TSM", "Taiwan Semiconductor (TSMC)", "Taiwan", "NYSE: TSM / TWSE: 2330", "Supplier — leading-edge foundry & CoWoS packaging", True),
    ("ARM", "Arm Holdings", "United Kingdom", "NASDAQ: ARM", "Ecosystem — CPU IP used in Grace / data-center CPUs", True),
    ("005930.KS", "Samsung Electronics", "South Korea", "KRX: 005930", "Supplier/rival — HBM & memory, foundry", False),
    ("000660.KS", "SK hynix", "South Korea", "KRX: 000660", "Supplier — primary HBM supplier for NVIDIA GPUs", False),
    ("688256.SS", "Cambricon Technologies", "China", "SSE: 688256", "Regional rival — AI accelerators for the China market", False),
]

PEER_CONCEPTS = {
    "revenue": [("us-gaap", "Revenues"), ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
                ("us-gaap", "SalesRevenueNet"), ("ifrs-full", "Revenue")],
    "gross_profit": [("us-gaap", "GrossProfit"), ("ifrs-full", "GrossProfit")],
    "operating_income": [("us-gaap", "OperatingIncomeLoss"), ("ifrs-full", "ProfitLossFromOperatingActivities")],
    "net_income": [("us-gaap", "NetIncomeLoss"), ("us-gaap", "ProfitLoss"),
                   ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"), ("ifrs-full", "ProfitLoss")],
    "rnd": [("us-gaap", "ResearchAndDevelopmentExpense"), ("ifrs-full", "ResearchAndDevelopmentExpense")],
    "total_assets": [("us-gaap", "Assets"), ("ifrs-full", "Assets")],
}


def d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def split_factor(filed: date) -> int:
    """How many current shares one share had become, for a figure filed on `filed`."""
    f = 1
    for when, ratio in SPLITS:
        if filed < when:
            f *= ratio
    return f


class Concept:
    """All facts of one metric, de-duplicated so the most recently filed value wins."""

    def __init__(self, gaap: dict, names: list, kind: str, key: str):
        self.dur, self.inst = {}, {}
        # Later candidates only fill gaps left by earlier (preferred) ones.
        for name in reversed(names):
            if name not in gaap:
                continue
            for unit_facts in gaap[name]["units"].values():
                for f in sorted(unit_facts, key=lambda x: x["filed"]):
                    val = f["val"]
                    if key == "eps_diluted":
                        val = val / split_factor(d(f["filed"]))
                    elif key == "diluted_shares":
                        val = val * split_factor(d(f["filed"]))
                    if "start" in f:
                        self.dur[(d(f["start"]), d(f["end"]))] = val
                    else:
                        self.inst[d(f["end"])] = val

    def get(self, start: date, end: date):
        if (start, end) in self.dur:
            return self.dur[(start, end)]
        for (s, e), v in self.dur.items():  # tolerate a few days of date drift
            if abs((s - start).days) <= 3 and abs((e - end).days) <= 3:
                return v
        return None

    def at(self, end: date):
        if end in self.inst:
            return self.inst[end]
        for e, v in self.inst.items():
            if abs((e - end).days) <= 3:
                return v
        return None


def fiscal_calendar(rev: Concept):
    """Fiscal years (from ~1-year revenue facts) and their four quarter boundaries."""
    years = sorted({(s, e) for (s, e) in rev.dur if 350 <= (e - s).days <= 380}, key=lambda p: p[1])
    # Add the in-progress fiscal year from YTD / quarterly facts beyond the last 10-K.
    last_end = years[-1][1] if years else date(2000, 1, 1)
    open_starts = {s for (s, e) in rev.dur if s > last_end and 80 <= (e - s).days <= 100 and (s - last_end).days <= 3}
    calendar = []
    for s, e in years + [(s, None) for s in open_starts]:
        ends = []
        for k in (1, 2, 3):
            target = s + timedelta(days=91 * k - 1)
            hits = [pe for (ps, pe) in rev.dur if abs((pe - target).days) <= 7 and (abs((ps - s).days) <= 3 or 80 <= (pe - ps).days <= 100)]
            ends.append(min(hits, key=lambda x: abs((x - target).days)) if hits else target)
        ends.append(e)
        calendar.append({"fy": (e or ends[2] + timedelta(days=91)).year, "start": s, "q_ends": ends})
    return sorted(calendar, key=lambda c: c["start"])


def periods_for(fy: dict):
    s, ends = fy["start"], fy["q_ends"]
    starts = [s] + [x + timedelta(days=1) for x in ends[:3]]
    for k in range(4):
        if ends[k] is not None:
            yield ("quarterly", f"Q{k + 1} FY{fy['fy']}", starts[k], ends[k], k + 1)
    for h in (1, 2):
        e = ends[2 * h - 1]
        if e is not None:
            yield ("half", f"H{h} FY{fy['fy']}", starts[2 * h - 2], e, h)
    if ends[3] is not None:
        yield ("annual", f"FY{fy['fy']}", s, ends[3], 0)


def flow_value(c: Concept, fy: dict, ps: date, pe: date):
    """Discrete value for [ps, pe], derived from YTD differences when not reported directly."""
    v = c.get(ps, pe)
    if v is not None:
        return v
    s, ends = fy["start"], fy["q_ends"]
    starts = [s] + [x + timedelta(days=1) for x in ends[:3]]

    def ytd(k):  # cumulative value through quarter k (1..4)
        if k == 0:
            return 0
        v = c.get(s, ends[k - 1])
        if v is not None:
            return v
        q, prev = c.get(starts[k - 1], ends[k - 1]), ytd(k - 1)
        return None if q is None or prev is None else prev + q

    k_end = next((k + 1 for k, e in enumerate(ends) if e == pe), None)
    k_start = next((k for k, st in enumerate(starts) if st == ps), None)
    if k_end is None or k_start is None:
        return None
    a, b = ytd(k_end), ytd(k_start)
    return None if a is None or b is None else a - b


def div(a, b):
    return None if a is None or b in (None, 0) else a / b


def growth(cur, prev):
    return None if cur is None or prev in (None, 0) else (cur - prev) / abs(prev)


def build_nvidia():
    facts = json.loads((RAW / "nvidia_companyfacts.json").read_text(encoding="utf-8"))
    gaap = facts["facts"]["us-gaap"]
    concepts = {k: Concept(gaap, names, kind, k) for k, (_, _, kind, names) in METRICS.items()}
    calendar = fiscal_calendar(concepts["revenue"])

    rows = []
    for fy in calendar:
        for freq, label, ps, pe, idx in periods_for(fy):
            row = {"frequency": freq, "period": label, "fiscal_year": fy["fy"], "index": idx,
                   "start": ps.isoformat(), "end": pe.isoformat()}
            for key, (_, _, kind, _) in METRICS.items():
                c = concepts[key]
                if kind == "instant":
                    row[key] = c.at(pe)
                elif kind == "flow":
                    row[key] = flow_value(c, fy, ps, pe)
                else:
                    row[key] = c.get(ps, pe)
            rows.append(row)

    # Derived per-period measures.
    for r in rows:
        if r["diluted_shares"] is None and r["frequency"] != "annual":
            # Q4 / H2 weighted shares are rarely tagged; fall back to the fiscal-year figure.
            fy_row = next((x for x in rows if x["frequency"] == "annual" and x["fiscal_year"] == r["fiscal_year"]), None)
            r["diluted_shares"] = fy_row and fy_row["diluted_shares"]
        if r["eps_diluted"] is None:
            r["eps_diluted"] = div(r["net_income"], r["diluted_shares"])
        if r["total_liabilities"] is None and r["total_assets"] is not None and r["equity"] is not None:
            r["total_liabilities"] = r["total_assets"] - r["equity"]
        if r["opex"] is None and r["rnd"] is not None and r["sga"] is not None:
            r["opex"] = r["rnd"] + r["sga"]
        r["fcf"] = None if r["cfo"] is None or r["capex"] is None else r["cfo"] - r["capex"]
        r["gross_margin"] = div(r["gross_profit"], r["revenue"])
        r["operating_margin"] = div(r["operating_income"], r["revenue"])
        r["net_margin"] = div(r["net_income"], r["revenue"])
        r["rnd_ratio"] = div(r["rnd"], r["revenue"])
        r["fcf_margin"] = div(r["fcf"], r["revenue"])
        pretax = None if r["net_income"] is None or r["income_tax"] is None else r["net_income"] + r["income_tax"]
        r["tax_rate"] = div(r["income_tax"], pretax)
        r["current_ratio"] = div(r["current_assets"], r["current_liabilities"])
        r["debt_to_equity"] = div(r["long_term_debt"], r["equity"])

    by_key = {(r["frequency"], r["fiscal_year"], r["index"]): r for r in rows}
    ordered = {f: [r for r in rows if r["frequency"] == f] for f in ("annual", "half", "quarterly")}
    for r in rows:
        prev = by_key.get((r["frequency"], r["fiscal_year"] - 1, r["index"]))
        r["revenue_yoy"] = growth(r["revenue"], prev and prev["revenue"])
        r["net_income_yoy"] = growth(r["net_income"], prev and prev["net_income"])
        r["eps_yoy"] = growth(r["eps_diluted"], prev and prev["eps_diluted"])
        r["revenue_qoq"] = None
        # Return ratios: annualise the period's income over average equity / assets.
        scale = {"annual": 1, "half": 2, "quarterly": 4}[r["frequency"]]
        lst = ordered[r["frequency"]]
        i = lst.index(r)
        before = lst[i - 1] if i > 0 else None
        avg_eq = None if r["equity"] is None else (r["equity"] + (before["equity"] if before and before["equity"] else r["equity"])) / 2
        avg_as = None if r["total_assets"] is None else (r["total_assets"] + (before["total_assets"] if before and before["total_assets"] else r["total_assets"])) / 2
        r["roe"] = div(r["net_income"] and r["net_income"] * scale, avg_eq)
        r["roa"] = div(r["net_income"] and r["net_income"] * scale, avg_as)
    q = ordered["quarterly"]
    for prev, cur in zip(q, q[1:]):
        cur["revenue_qoq"] = growth(cur["revenue"], prev["revenue"])

    # Drop leading periods that have no revenue at all (pre-XBRL comparatives).
    for f in ordered:
        ordered[f] = [r for r in ordered[f] if r["revenue"] is not None]
    return ordered, facts


def build_peers():
    out = []
    for ticker, name, country, listing, role, sec in PEERS:
        rec = {"ticker": ticker, "name": name, "country": country, "listing": listing, "role": role,
               "sec_filer": sec, "fiscal_year_end": None, "currency": None}
        path = RAW / ("nvidia_companyfacts.json" if ticker == "NVDA" else f"peers/{ticker}_companyfacts.json")
        if sec and path.exists():
            facts = json.loads(path.read_text(encoding="utf-8"))["facts"]
            latest_end = None
            values = {}
            for key, cands in PEER_CONCEPTS.items():
                best = None  # (end, filed, val, unit)
                for tax, concept in cands:
                    units = facts.get(tax, {}).get(concept, {}).get("units", {})
                    # Prefer USD (TSMC also tags a USD convenience translation).
                    for unit in sorted(units, key=lambda u: u != "USD"):
                        for f in units[unit]:
                            if key != "total_assets":
                                if "start" not in f or not 350 <= (d(f["end"]) - d(f["start"])).days <= 380:
                                    continue
                            elif f.get("form") not in ("10-K", "20-F", "10-K/A", "20-F/A"):
                                continue
                            cand = (d(f["end"]), unit == "USD", f["filed"], f["val"], unit)
                            if best is None or cand[:3] > best[:3]:
                                best = cand
                if best:
                    values[key] = best
            if "revenue" in values:
                latest_end = values["revenue"][0]
                rec["fiscal_year_end"] = latest_end.isoformat()
                rec["currency"] = values["revenue"][4]
                for key, (end, _, _, val, unit) in values.items():
                    # Only use figures from the same fiscal year as revenue, in the same currency.
                    rec[key] = val if abs((end - latest_end).days) <= 7 and unit == rec["currency"] else None
        for key in PEER_CONCEPTS:
            rec.setdefault(key, None)
        rec["gross_margin"] = div(rec["gross_profit"], rec["revenue"])
        rec["operating_margin"] = div(rec["operating_income"], rec["revenue"])
        rec["net_margin"] = div(rec["net_income"], rec["revenue"])
        rec["rnd_ratio"] = div(rec["rnd"], rec["revenue"])
        out.append(rec)
    return out


def insights(data):
    """Plain-language headline findings, regenerated with every update."""
    a, q = data["annual"], data["quarterly"]
    la, pa = a[-1], a[-2]
    lq = q[-1]
    out = []
    out.append(f"FY{la['fiscal_year']} 매출은 ${la['revenue'] / 1e9:,.1f}B로 전년 대비 {la['revenue_yoy'] * 100:+.0f}% "
               f"(FY{pa['fiscal_year']}: ${pa['revenue'] / 1e9:,.1f}B).")
    five = next((r for r in a if r["fiscal_year"] == la["fiscal_year"] - 5), None)
    if five and five["revenue"]:
        cagr = (la["revenue"] / five["revenue"]) ** (1 / 5) - 1
        out.append(f"최근 5년(FY{five['fiscal_year']}→FY{la['fiscal_year']}) 매출 CAGR {cagr * 100:.0f}%, "
                   f"순이익은 ${five['net_income'] / 1e9:,.1f}B → ${la['net_income'] / 1e9:,.1f}B.")
    out.append(f"최근 분기({lq['period']}, {lq['end']} 종료) 매출 ${lq['revenue'] / 1e9:,.1f}B "
               f"(YoY {(lq['revenue_yoy'] or 0) * 100:+.0f}%, QoQ {(lq['revenue_qoq'] or 0) * 100:+.0f}%), "
               f"매출총이익률 {lq['gross_margin'] * 100:.1f}%, 순이익률 {lq['net_margin'] * 100:.1f}%.")
    if la["fcf"] is not None:
        out.append(f"FY{la['fiscal_year']} 잉여현금흐름(FCF) ${la['fcf'] / 1e9:,.1f}B (매출 대비 {la['fcf_margin'] * 100:.0f}%), "
                   f"자사주 매입 ${(la['buybacks'] or 0) / 1e9:,.1f}B.")
    out.append(f"FY{la['fiscal_year']} R&D 비용 ${la['rnd'] / 1e9:,.1f}B (매출 대비 {la['rnd_ratio'] * 100:.1f}%, "
               f"전년 {pa['rnd_ratio'] * 100:.1f}%), 영업이익률 {la['operating_margin'] * 100:.0f}%.")
    return out


def update_readme(peers, data, generated_at):
    """Refresh the auto-generated blocks of README.md between their marker comments."""
    path = ROOT / "README.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")

    def pct(v):
        return "–" if v is None else f"{v * 100:.1f}%"

    def usd_m(v):
        return "–" if v is None else f"{v / 1e6:,.0f}"

    lines = ["| # | 기업 | 국가 | 상장 | NVIDIA와의 관계 | FY 종료 | 매출 (USD M) | 영업이익률 | 순이익률 | R&D/매출 |",
             "|---|---|---|---|---|---|---:|---:|---:|---:|"]
    for i, p in enumerate(peers, 1):
        name = f"**{p['name']}**" if p["ticker"] == "NVDA" else p["name"]
        fy = p["fiscal_year_end"] or ("–" if p["sec_filer"] else "SEC 미제출")
        lines.append(f"| {i} | {name} | {p['country']} | {p['listing']} | {p['role']} | {fy} | {usd_m(p['revenue'])} | "
                     f"{pct(p['operating_margin'])} | {pct(p['net_margin'])} | {pct(p['rnd_ratio'])} |")
    peers_md = "\n".join(lines)

    a, q = data["annual"][-1], data["quarterly"][-1]
    key_md = "\n".join([
        "| 구분 | 기간 | 매출 (USD M) | YoY | 매출총이익률 | 영업이익률 | 순이익 (USD M) | 희석 EPS |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
        *[f"| {lbl} | {r['period']} (~{r['end']}) | {usd_m(r['revenue'])} | {pct(r['revenue_yoy'])} | {pct(r['gross_margin'])} | "
          f"{pct(r['operating_margin'])} | {usd_m(r['net_income'])} | ${r['eps_diluted']:.2f} |"
          for lbl, r in (("최근 회계연도", a), ("최근 반기", data["half"][-1]), ("최근 분기", q))],
        "",
        f"_자동 업데이트: {generated_at}_",
    ])

    for tag, block in (("PEERS", peers_md), ("KEYFIGURES", key_md)):
        start, end = f"<!-- {tag}:START -->", f"<!-- {tag}:END -->"
        if start in text and end in text:
            head, rest = text.split(start, 1)
            text = head + start + "\n" + block + "\n" + end + rest.split(end, 1)[1]
    path.write_text(text, encoding="utf-8")


def write_csv(rows, path, keys):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main():
    data, facts = build_nvidia()
    peers = build_peers()
    sub = json.loads((RAW / "nvidia_submissions.json").read_text(encoding="utf-8"))
    filings = [f for f in sub["filings"] if f["form"] in ("10-K", "10-Q")][:12]
    cik = sub["cik"].lstrip("0")
    for f in filings:
        f["url"] = (f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                    f"{f['accessionNumber'].replace('-', '')}/{f['primaryDocument']}")

    meta_cols = ["period", "fiscal_year", "start", "end"]
    metric_cols = [k for _, keys in TABLE_LAYOUT for k in keys]
    for freq, name in (("annual", "annual"), ("half", "semiannual"), ("quarterly", "quarterly")):
        for folder in (OUT, DOCS_DATA):
            write_csv(data[freq], folder / f"nvidia_{name}.csv", meta_cols + metric_cols)
    for folder in (OUT, DOCS_DATA):
        write_csv(peers, folder / "peers.csv", list(peers[0].keys()))

    labels = {k: {"label": v[0], "unit": v[1]} for k, v in METRICS.items()}
    labels.update({k: {"label": v[0], "unit": v[1]} for k, v in DERIVED.items()})
    dashboard = {
        "company": {"name": facts["entityName"], "cik": sub["cik"], "ticker": "NVDA",
                    "sic": sub.get("sicDescription"), "fiscal_year_end": sub.get("fiscalYearEnd")},
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "latest_filing": filings[0] if filings else None,
        "filings": filings,
        "metrics": labels,
        "layout": [{"group": g, "keys": keys} for g, keys in TABLE_LAYOUT],
        "annual": data["annual"],
        "half": data["half"],
        "quarterly": data["quarterly"],
        "peers": peers,
        "insights": insights(data),
    }
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    # Keep the previous timestamp when nothing but the timestamp would change, so a run
    # without new SEC data leaves the repo untouched (no empty monthly commits).
    prev_path = DOCS_DATA / "dashboard.json"
    if prev_path.exists():
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
        candidate = json.loads(json.dumps(dashboard, default=str))
        if {**prev, "generated_at": None} == {**candidate, "generated_at": None}:
            dashboard["generated_at"] = prev["generated_at"]
    (DOCS_DATA / "dashboard.json").write_text(json.dumps(dashboard, indent=1, default=str), encoding="utf-8")
    update_readme(peers, data, dashboard["generated_at"])
    print(f"annual={len(data['annual'])} half={len(data['half'])} quarterly={len(data['quarterly'])} peers={len(peers)}")


if __name__ == "__main__":
    main()
