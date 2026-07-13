#!/usr/bin/env python3
"""
解析微信收藏数据（Favorite.db / CSV / JSON）为统一的 JSON 格式。
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime


# 微信收藏类型编码
TYPE_MAP = {
    1: "文本",
    2: "图片",
    3: "语音",
    4: "视频",
    5: "文章/链接",
    6: "位置",
    8: "文件",
    14: "聊天记录",
    16: "笔记",
    17: "小程序",
}


def _decode_xml_entities(s):
    """解码 XML/HTML 实体"""
    import html
    s = html.unescape(s)
    # 处理常见的 XML 数字实体
    s = re.sub(r'&#x([0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), s)
    return s


def _extract_xml_field(xml_str, tag):
    """从 XML content 中提取字段值"""
    if not xml_str:
        return ""
    m = re.search(rf'<{tag}>(.*?)</{tag}>', xml_str, re.DOTALL)
    return _decode_xml_entities(m.group(1).strip()) if m else ""


def _extract_xml_attr(xml_str, tag, attr):
    """从 XML content 中提取标签属性值"""
    if not xml_str:
        return ""
    import re
    m = re.search(rf'<{tag}\s[^>]*{attr}="([^"]*)"', xml_str)
    return m.group(1) if m else ""


def parse_sqlite(db_path):
    """解析解密后的 Favorite.db（兼容 WeChat 3.x FavItems 和 4.x fav_db_item）"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    items = []

    # 检测表名：WeChat 4.x 用 fav_db_item, 3.x 用 FavItems
    tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print(f"[INFO] 数据库表: {tables}", file=sys.stderr)

    if "fav_db_item" in tables:
        # WeChat 4.x 格式
        print("[INFO] 检测到 WeChat 4.x 格式 (fav_db_item)", file=sys.stderr)
        rows = conn.execute("""
            SELECT local_id, type, update_time, content, source_id, fromusr
            FROM fav_db_item
        """).fetchall()

        for row in rows:
            update_time = row["update_time"]
            if update_time and update_time > 0:
                try:
                    dt = datetime.fromtimestamp(update_time)
                    ts = dt.isoformat()
                except (ValueError, OSError):
                    ts = None
            else:
                ts = None

            content = row["content"] or ""
            fav_type = row["type"]
            title = ""
            desc = ""
            link = ""

            thumb = ""

            if fav_type == 5:
                # 文章/链接
                title = _extract_xml_field(content, "pagetitle")
                desc = _extract_xml_field(content, "pagedesc")
                link = (_extract_xml_field(content, "link")
                        or row["source_id"] or "")
                thumb = _extract_xml_field(content, "pagethumb_url")

            elif fav_type == 1:
                # 文本
                title = "文本笔记"
                desc = _extract_xml_field(content, "desc")

            elif fav_type == 14:
                # 聊天记录
                data_items = re.findall(r'<dataitem[^>]*>(.*?)</dataitem>', content, re.DOTALL)
                parts = []
                for di in data_items:
                    d = re.search(r'<datadesc>(.*?)</datadesc>', di, re.DOTALL)
                    s = re.search(r'<datasrcname>(.*?)</datasrcname>', di)
                    t = re.search(r'<datasrctime>(.*?)</datasrctime>', di)
                    d_text = _decode_xml_entities(d.group(1).strip()) if d else ""
                    s_text = _decode_xml_entities(s.group(1)) if s else ""
                    t_text = _decode_xml_entities(t.group(1)) if t else ""
                    if d_text:
                        line = f"{s_text} ({t_text}): {d_text}" if s_text else d_text
                        parts.append(line)
                if parts:
                    desc = "\n\n".join(parts)
                title = f"聊天记录 ({len(parts)} 条消息)"

            elif fav_type == 8:
                # 文件
                title = _extract_xml_field(content, "datatitle")
                fmt = _extract_xml_field(content, "datafmt")
                size_str = _extract_xml_field(content, "fullsize")
                size_mb = int(size_str) / 1024 / 1024 if size_str.isdigit() else 0
                desc = f"文件类型: {fmt.upper()}" if fmt else ""
                if size_mb > 0:
                    desc += f"\n文件大小: {size_mb:.1f} MB" if size_mb >= 1 else f"\n文件大小: {int(size_mb*1024)} KB"

            elif fav_type == 2:
                # 图片
                title = _extract_xml_field(content, "datatitle") or "收藏图片"
                fmt = _extract_xml_field(content, "datafmt") or "图片"
                size_str = _extract_xml_field(content, "fullsize")
                size_kb = int(size_str) / 1024 if size_str.isdigit() else 0
                src_name = _extract_xml_field(content, "realchatname") or _extract_xml_field(content, "fromusr")
                desc = _extract_xml_field(content, "datadesc") or ""
                meta_parts = [f"格式: {fmt}"]
                if size_kb > 0:
                    meta_parts.append(f"大小: {size_kb:.0f} KB")
                if src_name:
                    meta_parts.append(f"来自: {src_name}")
                if desc:
                    desc = desc + "\n" + " | ".join(meta_parts)
                else:
                    desc = " | ".join(meta_parts)

            elif fav_type == 4:
                # 视频
                title = _extract_xml_field(content, "datatitle") or "收藏视频"
                desc = _extract_xml_field(content, "datadesc") or ""
                src_name = _extract_xml_field(content, "realchatname") or _extract_xml_field(content, "fromusr")
                if src_name and src_name not in desc:
                    desc = (desc + f"\n来自: {src_name}") if desc else f"来自: {src_name}"

            elif fav_type == 3:
                # 语音
                title = "语音消息"
                desc = _extract_xml_field(content, "datadesc") or "语音收藏"

            elif fav_type == 6:
                # 位置
                title = _extract_xml_field(content, "poiname") or "位置收藏"
                label = _extract_xml_field(content, "poiaddress") or _extract_xml_field(content, "label")
                desc = label

            elif fav_type == 16:
                # 笔记
                title = _extract_xml_field(content, "datatitle") or "笔记"
                desc = _extract_xml_field(content, "datadesc")

            elif fav_type == 17:
                # 小程序
                title = _extract_xml_field(content, "pagetitle") or _extract_xml_field(content, "sourcename") or "小程序"
                desc = _extract_xml_field(content, "pagedesc")

            else:
                # 其他类型: 尝试所有可能的标题/描述字段
                title = (_extract_xml_field(content, "title")
                         or _extract_xml_field(content, "pagetitle")
                         or _extract_xml_field(content, "datatitle")
                         or _extract_xml_field(content, "nickname")
                         or "")
                desc = (_extract_xml_field(content, "desc")
                        or _extract_xml_field(content, "pagedesc")
                        or _extract_xml_field(content, "datadesc")
                        or "")

            # 通用缩略图提取
            if not thumb:
                thumb = (_extract_xml_field(content, "pagethumb_url")
                         or _extract_xml_field(content, "thumburl")
                         or "")
            # 只保留 http(s) 开头的有效 URL
            if thumb and not thumb.startswith("http"):
                thumb = ""

            # 标签
            tag_list = re.findall(r'<tag>(.*?)</tag>', content)
            source = row["fromusr"] or _extract_xml_field(content, "fromusr") or ""

            items.append({
                "id": row["local_id"],
                "type": row["type"],
                "type_name": TYPE_MAP.get(row["type"], "其他"),
                "source": source,
                "timestamp": ts,
                "title": title,
                "desc": desc,
                "link": link,
                "thumb": thumb,
                "search_key": "",
            })

        # 读取标签 (4.x: fav_tag_db_item)
        tags = {}
        if "fav_tag_db_item" in tables:
            try:
                tag_rows = conn.execute("SELECT * FROM fav_tag_db_item").fetchall()
                cols = [d[0] for d in tag_rows[0].keys()] if tag_rows else []
                for row in tag_rows:
                    # 列名: local_id, tag_id, tag_name, update_time
                    row_dict = dict(row)
                    tag_id = row_dict.get("local_id") or row_dict.get("tag_id")
                    tag_name = row_dict.get("tag_name", "")
                    # Check column by index if names don't match
                    raw = tuple(row)
                    if not tag_name and len(raw) >= 3:
                        tag_id = raw[0]
                        tag_name = raw[2]
                    if tag_id and tag_name:
                        tags[tag_id] = tag_name
            except (sqlite3.OperationalError, IndexError):
                pass

        # 读取标签绑定 (4.x: fav_bind_tag_db_item)
        tag_bindings = defaultdict(list)
        if "fav_bind_tag_db_item" in tables:
            try:
                bind_rows = conn.execute("SELECT * FROM fav_bind_tag_db_item").fetchall()
                for row in bind_rows:
                    raw = tuple(row)
                    # Columns: tag_local_id, tag_server_id, fav_local_id, fav_server_id, flag
                    if len(raw) >= 3:
                        tag_id = raw[0]
                        fav_id = raw[2]
                        if tag_id in tags:
                            tag_bindings[fav_id].append(tags[tag_id])
            except sqlite3.OperationalError:
                pass

    elif "FavItems" in tables:
        # WeChat 3.x 格式
        print("[INFO] 检测到 WeChat 3.x 格式 (FavItems)", file=sys.stderr)
        try:
            rows = conn.execute("""
                SELECT fi.FavLocalID, fi.Type, fi.FromUser, fi.UpdateTime,
                       fi.SearchKey, fd.Datatitle, fd.Datadesc
                FROM FavItems fi
                LEFT JOIN FavDataItem fd ON fi.FavLocalID = fd.FavLocalID
            """).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute("SELECT * FROM FavItems").fetchall()

        for row in rows:
            update_time = row["UpdateTime"] if "UpdateTime" in row.keys() else 0
            if update_time and update_time > 0:
                try:
                    dt = datetime.fromtimestamp(update_time)
                    ts = dt.isoformat()
                except (ValueError, OSError):
                    ts = None
            else:
                ts = None

            items.append({
                "id": row["FavLocalID"] if "FavLocalID" in row.keys() else 0,
                "type": row["Type"] if "Type" in row.keys() else 0,
                "type_name": TYPE_MAP.get(row["Type"] if "Type" in row.keys() else 0, "其他"),
                "source": row["FromUser"] if "FromUser" in row.keys() else "",
                "timestamp": ts,
                "title": row["Datatitle"] if "Datatitle" in row.keys() else "",
                "desc": row["Datadesc"] if "Datadesc" in row.keys() else "",
                "search_key": row["SearchKey"] if "SearchKey" in row.keys() else "",
            })

        # 读取标签 (3.x)
        tags = {}
        try:
            tag_rows = conn.execute("SELECT * FROM FavTagDatas").fetchall()
            for row in tag_rows:
                tags[row["LocalID"]] = row["TagName"]
        except sqlite3.OperationalError:
            pass

        tag_bindings = defaultdict(list)
        try:
            bind_rows = conn.execute("SELECT * FROM FavBindTagDatas").fetchall()
            for row in bind_rows:
                fav_id = row["FavLocalID"] if "FavLocalID" in row.keys() else None
                tag_id = row["TagLocalID"] if "TagLocalID" in row.keys() else None
                if fav_id and tag_id and tag_id in tags:
                    tag_bindings[fav_id].append(tags[tag_id])
        except sqlite3.OperationalError:
            pass
    else:
        print(f"[ERROR] 未找到收藏表 (FavItems 或 fav_db_item)", file=sys.stderr)
        sys.exit(1)

    # 合并标签到 items
    for item in items:
        item["tags"] = tag_bindings.get(item["id"], [])

    conn.close()
    return items


def parse_csv(csv_path):
    """解析 CSV 格式的收藏数据"""
    items = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            items.append({
                "id": i + 1,
                "type": int(row.get("type", 0)) if row.get("type", "").isdigit() else 0,
                "type_name": row.get("type_name", TYPE_MAP.get(int(row.get("type", 0)), "其他")),
                "source": row.get("source", ""),
                "timestamp": row.get("timestamp", None),
                "title": row.get("title", ""),
                "desc": row.get("desc", ""),
                "search_key": row.get("search_key", ""),
                "tags": [t.strip() for t in row.get("tags", "").split(",") if t.strip()],
            })
    return items


def parse_json(json_path):
    """解析 JSON 格式的收藏数据"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = []
    for i, row in enumerate(data):
        items.append({
            "id": row.get("id", i + 1),
            "type": row.get("type", 0),
            "type_name": row.get("type_name", TYPE_MAP.get(row.get("type", 0), "其他")),
            "source": row.get("source", ""),
            "timestamp": row.get("timestamp", None),
            "title": row.get("title", ""),
            "desc": row.get("desc", ""),
            "search_key": row.get("search_key", ""),
            "tags": row.get("tags", []),
        })
    return items


def compute_stats(items):
    """计算统计数据"""
    total = len(items)
    if total == 0:
        return {"total": 0, "error": "没有收藏数据"}

    # 时间范围
    timestamps = [it["timestamp"] for it in items if it["timestamp"]]
    if timestamps:
        dates = sorted(timestamps)
        first_date = dates[0][:10]
        last_date = dates[-1][:10]
        first_dt = datetime.fromisoformat(first_date)
        last_dt = datetime.fromisoformat(last_date)
        span_days = (last_dt - first_dt).days + 1
    else:
        first_date = "未知"
        last_date = "未知"
        span_days = 0

    # 类型分布
    type_counter = Counter(it["type_name"] for it in items)
    type_dist = [{"name": k, "value": v} for k, v in type_counter.most_common()]

    # 来源排行
    source_counter = Counter(it["source"] for it in items if it["source"])
    source_top = [{"name": k, "value": v} for k, v in source_counter.most_common(20)]

    # 月度趋势
    monthly = Counter()
    daily = Counter()
    hourly = Counter()
    weekday = Counter()
    for it in items:
        if it["timestamp"]:
            try:
                dt = datetime.fromisoformat(it["timestamp"])
                monthly[dt.strftime("%Y-%m")] += 1
                daily[dt.strftime("%Y-%m-%d")] += 1
                hourly[dt.hour] += 1
                weekday[dt.weekday()] += 1
            except ValueError:
                pass

    monthly_sorted = sorted(monthly.items())
    monthly_trend = [{"month": k, "count": v} for k, v in monthly_sorted]

    # 每小时分布
    hour_dist = [{"hour": h, "count": hourly.get(h, 0)} for h in range(24)]

    # 星期分布
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday_dist = [{"day": weekday_names[d], "count": weekday.get(d, 0)} for d in range(7)]

    # 收藏最多的一天
    if daily:
        busiest_day, busiest_count = daily.most_common(1)[0]
    else:
        busiest_day, busiest_count = "未知", 0

    # 关键词（从标题和描述提取）
    text_all = " ".join(it["title"] + " " + it["desc"] for it in items)
    # 简单分词：按非中文/英文字符分割，过滤短词
    words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', text_all)
    # 过滤常见停用词
    stopwords = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
        "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
        "自己", "这", "他", "她", "它", "这个", "那个", "什么", "怎么", "可以", "没",
        "来", "与", "为", "及", "等", "从", "被", "把", "让", "向", "于", "以", "中",
        "大", "小", "多", "少", "更", "最", "已", "还", "而", "但", "如", "如果",
        "因为", "所以", "虽然", "但是", "或者", "还是", "只", "才", "又", "再",
        "the", "and", "for", "that", "this", "with", "from", "are", "was", "were",
        "been", "have", "has", "had", "will", "would", "could", "should", "can",
        "not", "but", "all", "each", "which", "their", "there", "then", "than",
        "http", "https", "www", "com", "org", "html",
    }
    word_counter = Counter(w.lower() for w in words if w.lower() not in stopwords)
    keywords = [{"name": k, "value": v} for k, v in word_counter.most_common(80)]

    # 标签分布
    tag_counter = Counter()
    for it in items:
        for tag in it.get("tags", []):
            tag_counter[tag] += 1
    tag_dist = [{"name": k, "value": v} for k, v in tag_counter.most_common(30)]

    # 热力图数据（星期 x 小时）
    heatmap_data = []
    heatmap_counter = Counter()
    for it in items:
        if it["timestamp"]:
            try:
                dt = datetime.fromisoformat(it["timestamp"])
                heatmap_counter[(dt.weekday(), dt.hour)] += 1
            except ValueError:
                pass
    for wd in range(7):
        for h in range(24):
            heatmap_data.append([h, wd, heatmap_counter.get((wd, h), 0)])

    return {
        "total": total,
        "first_date": first_date,
        "last_date": last_date,
        "span_days": span_days,
        "daily_avg": round(total / max(span_days, 1), 1),
        "type_dist": type_dist,
        "source_top": source_top,
        "monthly_trend": monthly_trend,
        "hour_dist": hour_dist,
        "weekday_dist": weekday_dist,
        "heatmap": heatmap_data,
        "busiest_day": busiest_day,
        "busiest_count": busiest_count,
        "keywords": keywords,
        "tag_dist": tag_dist,
        "has_tags": len(tag_dist) > 0,
    }


def main():
    parser = argparse.ArgumentParser(description="解析微信收藏数据")
    parser.add_argument("--input", "-i", required=True, help="输入文件路径 (.db / .csv / .json)")
    parser.add_argument("--output", "-o", required=True, help="输出 JSON 文件路径")
    args = parser.parse_args()

    input_path = os.path.expanduser(args.input)
    output_path = os.path.expanduser(args.output)

    if not os.path.exists(input_path):
        print(f"[ERROR] 输入文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".db":
        print(f"[INFO] 检测到 SQLite 数据库: {input_path}")
        items = parse_sqlite(input_path)
    elif ext == ".csv":
        print(f"[INFO] 检测到 CSV 文件: {input_path}")
        items = parse_csv(input_path)
    elif ext == ".json":
        print(f"[INFO] 检测到 JSON 文件: {input_path}")
        items = parse_json(input_path)
    else:
        print(f"[ERROR] 不支持的文件格式: {ext}（支持 .db / .csv / .json）", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] 解析到 {len(items)} 条收藏记录")

    stats = compute_stats(items)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"items": items, "stats": stats}, f, ensure_ascii=False, indent=2)

    print(f"[OK] 数据已保存到: {output_path}")


if __name__ == "__main__":
    main()
