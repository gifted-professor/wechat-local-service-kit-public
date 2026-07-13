#!/usr/bin/env python3
"""
生成模拟的微信收藏数据（JSON 格式），用于测试和演示。
"""

import json
import random
import os
from datetime import datetime, timedelta

SOURCES = [
    "机器之心", "少数派", "36氪", "量子位", "InfoQ",
    "极客公园", "虎嗅", "爱范儿", "品玩", "PingWest",
    "Y Combinator", "TechCrunch", "阮一峰的网络日志", "酷壳",
    "美团技术团队", "阿里技术", "字节跳动技术", "腾讯技术工程",
    "张三", "李四", "工作群", "AI讨论群", "读书分享群",
]

TITLES_ARTICLES = [
    "GPT-5 发布：多模态能力全面升级",
    "AI 编程助手深度评测：代码能力对比",
    "MCP 协议详解：AI 工具的连接标准",
    "2025 年前端框架对比：React vs Vue vs Svelte",
    "如何用 Python 做数据分析",
    "Rust 入门指南：系统编程的未来",
    "独立开发者如何做到月入十万",
    "从零到一：AI 出海实战手册",
    "Anthropic 的 AI 安全研究方向",
    "LangChain 实战：构建 RAG 应用",
    "Cursor 编辑器深度使用技巧",
    "远程工作三年经验总结",
    "如何建立个人知识管理系统",
    "深度学习论文精选：Transformer 变体",
    "Cloudflare Workers 实战指南",
    "产品设计的第一性原理",
    "如何写出让用户上瘾的文案",
    "SEO 入门到精通完全指南",
    "微信小程序性能优化实践",
    "TypeScript 5.0 新特性解读",
    "Redis 缓存设计最佳实践",
    "Kubernetes 部署自动化",
    "用 Notion 构建团队协作流程",
    "设计系统搭建实战",
    "如何做好技术选型",
]

TITLES_NOTES = [
    "会议笔记：Q3 产品规划",
    "读书笔记：思考快与慢",
    "学习计划：2025 下半年",
    "灵感记录：App 设计思路",
    "TODO：本周待办事项",
]

TAGS = ["AI", "编程", "设计", "产品", "读书", "工具", "效率", "创业", "技术", "前端", "后端", "数据"]

def generate_demo_data(count=500):
    items = []
    base_date = datetime(2023, 3, 1)
    end_date = datetime(2026, 4, 10)
    total_days = (end_date - base_date).days

    for i in range(count):
        # 随机时间，周末和晚上收藏更多
        day_offset = random.randint(0, total_days)
        dt = base_date + timedelta(days=day_offset)
        # 偏向晚上收藏
        hour_weights = [1]*6 + [3]*2 + [5]*4 + [3]*2 + [2]*2 + [4]*3 + [8]*3 + [5]*2
        hour = random.choices(range(24), weights=hour_weights)[0]
        minute = random.randint(0, 59)
        dt = dt.replace(hour=hour, minute=minute)

        # 类型分布：文章最多
        type_choices = [5]*60 + [2]*15 + [4]*8 + [16]*7 + [1]*5 + [8]*3 + [14]*2
        fav_type = random.choice(type_choices)

        type_map = {1:"文本", 2:"图片", 3:"语音", 4:"视频", 5:"文章/链接", 8:"文件", 14:"聊天记录", 16:"笔记"}

        if fav_type == 5:
            title = random.choice(TITLES_ARTICLES)
            desc = f"来自 {random.choice(SOURCES)} 的深度文章"
        elif fav_type == 16:
            title = random.choice(TITLES_NOTES)
            desc = "个人笔记"
        elif fav_type == 2:
            title = f"图片_{i}"
            desc = "收藏的图片"
        elif fav_type == 4:
            title = f"视频_{i}"
            desc = "收藏的视频"
        elif fav_type == 1:
            title = f"文本消息"
            desc = random.choice(["有道理", "mark一下", "转发收藏", "好文推荐", "值得学习"])
        elif fav_type == 8:
            title = random.choice(["季度报告.pdf", "设计稿v3.sketch", "需求文档.docx", "数据分析.xlsx"])
            desc = "收藏的文件"
        else:
            title = f"聊天记录_{i}"
            desc = "合并转发的聊天记录"

        source = random.choice(SOURCES)
        item_tags = random.sample(TAGS, k=random.randint(0, 3)) if random.random() > 0.6 else []

        items.append({
            "id": i + 1,
            "type": fav_type,
            "type_name": type_map.get(fav_type, "其他"),
            "source": source,
            "timestamp": dt.isoformat(),
            "title": title,
            "desc": desc,
            "search_key": "",
            "tags": item_tags,
        })

    return items


if __name__ == "__main__":
    items = generate_demo_data(500)
    output_path = os.path.expanduser("~/Downloads/wechat-favorites-report/demo_raw.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"[OK] 生成了 {len(items)} 条模拟数据: {output_path}")
