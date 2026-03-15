#!/usr/bin/env python3
import datetime as dt
import hashlib
import html
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple

SCORE_WEIGHT = {"S": 4, "A": 3, "B": 2, "C": 1}
TIER_WEIGHT = {"A": 3, "B": 2, "C": 1}


class FetchError(Exception):
    pass


def load_watchlist(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; DailyIntelBot/1.0)",
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def fetch_google_news_rss(query: str) -> List[dict]:
    q = urllib.parse.quote(query)
    urls = [
        f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
        f"https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]
    last_error = None
    for url in urls:
        try:
            data = _http_get(url)
            root = ET.fromstring(data)
            items = []
            for item in root.findall("./channel/item"):
                title = html.unescape(item.findtext("title", default="").strip())
                link = item.findtext("link", default="").strip()
                pub = item.findtext("pubDate", default="").strip()
                source = item.findtext("source", default="").strip()
                items.append({"title": title, "link": link, "pubDate": pub, "source": source})
            if items:
                return items
        except Exception as e:
            last_error = e
    raise FetchError(str(last_error) if last_error else "unknown error")


def normalize_text(s: str) -> str:
    s = s.lower()
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def event_key(item: dict) -> str:
    base = normalize_text(item.get("title", ""))
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def pick_event_tag(title: str, rules: dict) -> str:
    txt = normalize_text(title)
    for tag, kws in rules.items():
        for kw in kws:
            if normalize_text(kw) in txt:
                return tag
    return "其他"


def source_quality_rank(source: str, high_quality_sources: List[str]) -> int:
    s = source.lower()
    for idx, hq in enumerate(high_quality_sources):
        if hq.lower() in s:
            return 100 - idx
    return 10


def detect_company_and_tier(title: str, companies: List[dict]) -> Tuple[str, str, List[str]]:
    t = title.lower()
    for c in companies:
        name = c["name"]
        aliases = set(c.get("aliases", []))
        aliases.add(name)
        if name.upper() == "BYD":
            aliases.update(["比亚迪", "BYD"])
        for alias in aliases:
            if alias.lower() in t:
                return name, c.get("tier", "C"), c.get("a_share_mapping", [])[:3]
    return "未识别", "C", []


def is_price_noise(title: str, noise_keywords: List[str]) -> bool:
    t = normalize_text(title)
    return any(normalize_text(k) in t for k in noise_keywords)


def priority_score(tier: str, source: str, tag: str) -> str:
    score = TIER_WEIGHT.get(tier, 1)
    if tag in {"财报", "订单", "政策", "Capex", "临床"}:
        score += 1
    if "reuters" in source.lower() or "bloomberg" in source.lower():
        score += 1
    if score >= 5:
        return "S"
    if score >= 4:
        return "A"
    if score >= 3:
        return "B"
    return "C"


def worth_watching(tag: str, tier: str, sector: str) -> str:
    if tier == "A":
        return f"A级跟踪标的，{tag}变化往往先影响{sector}链条预期。"
    if tag in {"财报", "临床", "Capex"}:
        return f"该{tag}对未来1-2个季度业绩预期敏感。"
    return "事件可能影响板块风险偏好与交易节奏。"


def summarize(title: str) -> str:
    clean = re.sub(r"\s*-\s*[^-]+$", "", title).strip()
    return clean[:80]


def score_to_num(s: str) -> int:
    return SCORE_WEIGHT.get(s, 0)


def select_top_items(all_items: List[dict], top_n: int) -> List[dict]:
    return sorted(all_items, key=lambda x: (score_to_num(x["priority"]), x["source_rank"]), reverse=True)[:top_n]


def build_reports(watchlist: dict) -> Tuple[str, str, bool]:
    bj_now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    min_score = watchlist.get("min_score_for_feishu", "B")

    all_items: List[dict] = []
    sector_summary: Dict[str, List[dict]] = {}
    raw_links = []
    failures = []
    seen_events = set()

    for sector in watchlist["sectors"]:
        sector_name = sector["name"]
        sector_items = []
        for query in sector.get("queries", []):
            try:
                rss_items = fetch_google_news_rss(query)
            except Exception as e:
                failures.append(f"{sector_name}: {query} -> {e}")
                continue

            for r in rss_items:
                if is_price_noise(r["title"], watchlist.get("price_noise_keywords", [])):
                    continue

                e_key = event_key(r)
                if e_key in seen_events:
                    continue
                seen_events.add(e_key)

                company, tier, a_map = detect_company_and_tier(r["title"], sector.get("companies", []))
                tag = pick_event_tag(r["title"], watchlist.get("event_tag_rules", {}))
                pr = priority_score(tier, r.get("source", ""), tag)
                item = {
                    "sector": sector_name,
                    "company": company,
                    "tier": tier,
                    "event_tag": tag,
                    "summary": summarize(r["title"]),
                    "why": worth_watching(tag, tier, sector_name),
                    "a_share_mapping": a_map,
                    "link": r.get("link", ""),
                    "priority": pr,
                    "source": r.get("source", ""),
                    "pubDate": r.get("pubDate", ""),
                    "source_rank": source_quality_rank(r.get("source", ""), watchlist.get("high_quality_sources", [])),
                }
                sector_items.append(item)
                raw_links.append(item)

        sector_items = sorted(sector_items, key=lambda x: (score_to_num(x["priority"]), x["source_rank"]), reverse=True)
        sector_summary[sector_name] = sector_items[: watchlist.get("max_sector_items", 4)]
        all_items.extend(sector_items)

    all_failed = len(all_items) == 0
    top_items = select_top_items(all_items, watchlist.get("max_top_items", 10))
    company_pool = [
        x
        for x in sorted(
            all_items,
            key=lambda t: (TIER_WEIGHT.get(t["tier"], 1), score_to_num(t["priority"]), t["source_rank"]),
            reverse=True,
        )
        if x["company"] != "未识别"
    ]
    company_pool = company_pool[: watchlist.get("max_company_moves", 12)]

    radar_lines = []
    for sector in watchlist["sectors"]:
        sname = sector["name"]
        positive = set(sector.get("radar_rules", {}).get("positive_tags", []))
        negative = set(sector.get("radar_rules", {}).get("negative_tags", []))
        score = 0
        for it in sector_summary.get(sname, []):
            if it["event_tag"] in positive:
                score += 1
            if it["event_tag"] in negative:
                score -= 1
        state = "中性"
        if score >= 2:
            state = "偏强"
        elif score <= -1:
            state = "偏弱"
        radar_lines.append(f"- {sname}：{state}")

    today = bj_now.date()
    max_day = today + dt.timedelta(days=7)
    event_preview = []
    for ev in watchlist.get("upcoming_events", []):
        try:
            d = dt.datetime.strptime(ev["date"], "%Y-%m-%d").date()
        except Exception:
            continue
        if today <= d <= max_day:
            event_preview.append(ev)

    full = []
    full.append(f"# 盘前全球行业情报晨报（{bj_now:%Y-%m-%d %H:%M} 北京时间）")
    if all_failed:
        full.append("\n> ⚠️ 今日全部板块抓取失败，请检查网络/源站可用性。")
    full.append("\n## 1) 今日最高优先级（5-10条）")
    for i, it in enumerate(top_items, 1):
        full.append(
            f"{i}. [{it['priority']}] [{it['sector']}] {it['company']} | {it['event_tag']}\n"
            f"   - 摘要：{it['summary']}\n"
            f"   - 为什么值得看：{it['why']}\n"
            f"   - A股映射：{', '.join(it['a_share_mapping']) if it['a_share_mapping'] else '暂无'}\n"
            f"   - 来源：{it['source']}\n"
            f"   - 链接：{it['link']}"
        )

    full.append("\n## 2) 五大板块摘要")
    for sname, items in sector_summary.items():
        full.append(f"\n### {sname}")
        if not items:
            full.append("- 暂无有效情报")
            continue
        for it in items:
            full.append(f"- [{it['priority']}] {it['summary']} ({it['company']} / {it['event_tag']})")

    full.append("\n## 3) 重点公司异动池")
    for it in company_pool:
        full.append(f"- {it['company']}({it['tier']}) | {it['sector']} | {it['event_tag']} | {it['summary']} | {it['link']}")

    full.append("\n## 4) 原文链接池")
    for it in raw_links[:80]:
        full.append(f"- [{it['sector']}] {it['summary']} -> {it['link']}")

    full.append("\n## 主线雷达")
    full.extend(radar_lines)

    full.append("\n## 未来7天重点事件预告")
    if event_preview:
        for ev in event_preview:
            full.append(f"- {ev['date']} | {ev['sector']} | {ev['title']} | {ev['why']}")
    else:
        full.append("- 暂无未来7天配置事件")

    if failures:
        full.append("\n## 抓取告警")
        full.extend([f"- {x}" for x in failures[:30]])

    full_text = "\n".join(full)

    concise = []
    concise.append(f"【盘前情报精华】{bj_now:%m-%d %H:%M}")
    concise.append("主线雷达：" + "；".join([x.replace("- ", "") for x in radar_lines]))

    selected = [x for x in top_items if score_to_num(x["priority"]) >= score_to_num(min_score)]
    if not selected:
        selected = top_items[:5]

    for i, it in enumerate(selected[:8], 1):
        concise.append(f"{i}) [{it['priority']}] {it['sector']} | {it['company']} | {it['summary']}")

    if all_failed:
        concise.append("⚠️ 全部板块抓取失败，请检查网络连通性/数据源可用性。")

    concise_text = "\n".join(concise)
    return concise_text, full_text, all_failed


def write_full_report(full_text: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    bj_now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    filename = f"daily_brief_{bj_now:%Y%m%d_%H%M}.md"
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(full_text)
    return path


def publish_markdown_if_possible(path: str) -> str:
    if os.getenv("PASTE_RS_DISABLE", "0") == "1":
        return ""
    try:
        with open(path, "rb") as f:
            data = f.read()
        req = urllib.request.Request("https://paste.rs", data=data, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def send_to_feishu(webhook: str, text: str):
    payload = {"msg_type": "text", "content": {"text": text[:29000]}}
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def persist_failed_push(root: str, msg: str, err: str) -> str:
    out_dir = os.path.join(root, "output")
    os.makedirs(out_dir, exist_ok=True)
    bj_now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    path = os.path.join(out_dir, f"failed_feishu_push_{bj_now:%Y%m%d_%H%M}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("[Feishu push failed]\n")
        f.write(err + "\n\n")
        f.write(msg)
    return path


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    watchlist = load_watchlist(os.path.join(root, "config", "watchlist.json"))

    concise, full, all_failed = build_reports(watchlist)
    full_path = write_full_report(full, os.path.join(root, "output"))
    public_link = publish_markdown_if_possible(full_path)

    footer = f"\n\n全量Markdown：{public_link or full_path}"
    msg = concise + footer

    webhook = os.getenv("FEISHU_WEBHOOK")
    if not webhook:
        print(msg)
        raise SystemExit("Missing FEISHU_WEBHOOK environment variable")

    try:
        resp = send_to_feishu(webhook, msg)
        print("Feishu response:", resp)
        if all_failed:
            print("Warning: all sectors failed; failure notification sent.")
    except Exception as e:
        fail_path = persist_failed_push(root, msg, str(e))
        raise SystemExit(f"Feishu push failed: {e}. Saved message to {fail_path}")


if __name__ == "__main__":
    main()
