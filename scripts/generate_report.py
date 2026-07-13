#!/usr/bin/env python3
"""
从解析后的 JSON 数据生成微信收藏可视化 HTML 报告。
单文件输出，内联 ECharts CDN，无需额外依赖。
"""

import argparse
import json
import os
import sys


def generate_html(stats, items=None, title="我的微信收藏报告"):
    """生成完整的 HTML 报告"""

    items = items or []
    # 为浏览区准备精简的 items 数据（去掉 search_key 等大字段节省体积）
    browse_items = []
    for it in items:
        browse_items.append({
            "id": it.get("id", 0),
            "type_name": it.get("type_name", "其他"),
            "source": it.get("source", ""),
            "timestamp": it.get("timestamp", ""),
            "title": it.get("title", ""),
            "desc": it.get("desc", "") or "",
            "link": it.get("link", "") or "",
            "thumb": it.get("thumb", "") or "",
            "tags": it.get("tags", []),
        })
    items_json = json.dumps(browse_items, ensure_ascii=False)

    type_dist_json = json.dumps(stats["type_dist"], ensure_ascii=False)
    source_top_json = json.dumps(stats["source_top"][:15], ensure_ascii=False)
    monthly_json = json.dumps(stats["monthly_trend"], ensure_ascii=False)
    hour_json = json.dumps(stats["hour_dist"], ensure_ascii=False)
    weekday_json = json.dumps(stats["weekday_dist"], ensure_ascii=False)
    keywords_json = json.dumps(stats["keywords"][:60], ensure_ascii=False)
    heatmap_json = json.dumps(stats["heatmap"], ensure_ascii=False)
    tag_json = json.dumps(stats.get("tag_dist", []), ensure_ascii=False)
    has_tags = stats.get("has_tags", False)

    # 计算亮点
    top_source = stats["source_top"][0]["name"] if stats["source_top"] else "无"
    top_source_count = stats["source_top"][0]["value"] if stats["source_top"] else 0
    top_type = stats["type_dist"][0]["name"] if stats["type_dist"] else "无"
    top_type_pct = round(stats["type_dist"][0]["value"] / max(stats["total"], 1) * 100) if stats["type_dist"] else 0

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts-wordcloud@2/dist/echarts-wordcloud.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: #0a0e27;
    color: #e0e6ff;
    min-height: 100vh;
  }}
  .header {{
    text-align: center;
    padding: 60px 20px 40px;
    background: linear-gradient(135deg, #0a0e27 0%, #1a1f4e 50%, #0a0e27 100%);
  }}
  .header h1 {{
    font-size: 2.5em;
    font-weight: 700;
    background: linear-gradient(135deg, #60a5fa, #a78bfa, #f472b6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }}
  .header .subtitle {{
    color: #8b95c9;
    font-size: 1.1em;
  }}

  /* 数字卡片 */
  .stats-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 20px;
    max-width: 1200px;
    margin: -20px auto 40px;
    padding: 0 30px;
  }}
  .stat-card {{
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 28px 24px;
    text-align: center;
    backdrop-filter: blur(10px);
    transition: transform 0.2s, border-color 0.2s;
  }}
  .stat-card:hover {{
    transform: translateY(-4px);
    border-color: rgba(96,165,250,0.3);
  }}
  .stat-card .number {{
    font-size: 2.8em;
    font-weight: 800;
    background: linear-gradient(135deg, #60a5fa, #a78bfa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    line-height: 1.2;
  }}
  .stat-card .label {{
    color: #8b95c9;
    font-size: 0.95em;
    margin-top: 8px;
  }}

  /* 图表容器 */
  .container {{
    max-width: 1200px;
    margin: 0 auto;
    padding: 0 30px 60px;
  }}
  .chart-row {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
    margin-bottom: 24px;
  }}
  .chart-full {{
    margin-bottom: 24px;
  }}
  .chart-box {{
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 24px;
    backdrop-filter: blur(10px);
  }}
  .chart-box h3 {{
    font-size: 1.15em;
    font-weight: 600;
    color: #c4cbf5;
    margin-bottom: 16px;
    padding-left: 12px;
    border-left: 3px solid #60a5fa;
  }}
  .chart {{
    width: 100%;
    height: 360px;
  }}
  .chart-tall {{
    height: 450px;
  }}

  /* 亮点卡片 */
  .highlights {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 16px;
    margin-bottom: 40px;
  }}
  .highlight {{
    background: linear-gradient(135deg, rgba(96,165,250,0.1), rgba(167,139,250,0.1));
    border: 1px solid rgba(96,165,250,0.2);
    border-radius: 12px;
    padding: 20px;
  }}
  .highlight .hl-label {{
    color: #8b95c9;
    font-size: 0.85em;
    margin-bottom: 6px;
  }}
  .highlight .hl-value {{
    font-size: 1.3em;
    font-weight: 700;
    color: #e0e6ff;
  }}
  .highlight .hl-detail {{
    color: #60a5fa;
    font-size: 0.9em;
    margin-top: 4px;
  }}

  /* 分类浏览区 */
  .browse-section {{
    max-width: 1200px;
    margin: 0 auto;
    padding: 0 30px 60px;
  }}
  .browse-header {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 20px;
    flex-wrap: wrap;
  }}
  .browse-header h2 {{
    font-size: 1.4em;
    font-weight: 700;
    color: #c4cbf5;
    margin-right: 12px;
  }}
  .filter-group {{
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px;
    padding: 12px 16px;
    margin-bottom: 12px;
  }}
  .filter-label {{
    color: #4a5280;
    font-size: 0.75em;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 8px;
    display: block;
  }}
  .tab-bar {{
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    align-items: center;
  }}
  .tab-btn {{
    padding: 4px 12px;
    border-radius: 6px;
    border: 1px solid rgba(255,255,255,0.08);
    background: rgba(255,255,255,0.03);
    color: #6b7394;
    font-size: 0.8em;
    cursor: pointer;
    transition: all 0.2s;
    white-space: nowrap;
    line-height: 1.6;
  }}
  .tab-btn:hover {{
    border-color: rgba(96,165,250,0.3);
    color: #c4cbf5;
    background: rgba(96,165,250,0.06);
  }}
  .tab-btn.active {{
    background: rgba(96,165,250,0.15);
    border-color: #60a5fa;
    color: #60a5fa;
    font-weight: 600;
  }}
  .tab-btn .tab-count {{
    font-size: 0.8em;
    opacity: 0.5;
    margin-left: 3px;
  }}
  #tag-tabs .tab-btn {{
    border-radius: 12px;
    padding: 3px 10px;
    font-size: 0.75em;
    color: #a78bfa;
    border-color: rgba(167,139,250,0.15);
    background: rgba(167,139,250,0.04);
  }}
  #tag-tabs .tab-btn:hover {{
    border-color: rgba(167,139,250,0.3);
    background: rgba(167,139,250,0.08);
  }}
  #tag-tabs .tab-btn.active {{
    background: rgba(167,139,250,0.2);
    border-color: #a78bfa;
    color: #a78bfa;
  }}
  .search-row {{
    display: flex;
    gap: 12px;
    margin-bottom: 20px;
    flex-wrap: wrap;
  }}
  .search-input {{
    flex: 1;
    min-width: 200px;
    padding: 10px 16px;
    border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.1);
    background: rgba(255,255,255,0.04);
    color: #e0e6ff;
    font-size: 0.95em;
    outline: none;
    transition: border-color 0.2s;
  }}
  .search-input:focus {{
    border-color: #60a5fa;
  }}
  .search-input::placeholder {{
    color: #4a5280;
  }}
  .sort-select {{
    padding: 10px 16px;
    border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.1);
    background: rgba(255,255,255,0.04);
    color: #8b95c9;
    font-size: 0.9em;
    cursor: pointer;
    outline: none;
  }}
  .browse-info {{
    color: #4a5280;
    font-size: 0.85em;
    margin-bottom: 16px;
  }}
  .items-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }}
  .item-card {{
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px;
    padding: 20px;
    transition: border-color 0.2s, transform 0.15s;
    cursor: pointer;
  }}
  .item-card:hover {{
    border-color: rgba(96,165,250,0.25);
    transform: translateY(-2px);
  }}
  .item-top {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
  }}
  .item-type-badge {{
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.75em;
    font-weight: 600;
    white-space: nowrap;
  }}
  .badge-article {{ background: rgba(96,165,250,0.15); color: #60a5fa; }}
  .badge-image {{ background: rgba(244,114,182,0.15); color: #f472b6; }}
  .badge-video {{ background: rgba(251,191,36,0.15); color: #fbbf24; }}
  .badge-text {{ background: rgba(52,211,153,0.15); color: #34d399; }}
  .badge-file {{ background: rgba(251,146,60,0.15); color: #fb923c; }}
  .badge-note {{ background: rgba(167,139,250,0.15); color: #a78bfa; }}
  .badge-chat {{ background: rgba(56,189,248,0.15); color: #38bdf8; }}
  .badge-other {{ background: rgba(139,149,201,0.12); color: #8b95c9; }}
  .item-date {{
    color: #4a5280;
    font-size: 0.8em;
    margin-left: auto;
  }}
  .item-title {{
    font-size: 1em;
    font-weight: 600;
    color: #e0e6ff;
    margin-bottom: 6px;
    line-height: 1.4;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }}
  .item-desc {{
    font-size: 0.85em;
    color: #6b7394;
    line-height: 1.5;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }}
  .item-bottom {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 10px;
    flex-wrap: wrap;
  }}
  .item-source {{
    font-size: 0.8em;
    color: #60a5fa;
    background: rgba(96,165,250,0.08);
    padding: 2px 8px;
    border-radius: 6px;
  }}
  .item-tag {{
    font-size: 0.75em;
    color: #a78bfa;
    background: rgba(167,139,250,0.1);
    padding: 2px 8px;
    border-radius: 6px;
  }}
  .pagination {{
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 8px;
    margin-top: 24px;
  }}
  .page-btn {{
    padding: 8px 16px;
    border-radius: 8px;
    border: 1px solid rgba(255,255,255,0.1);
    background: rgba(255,255,255,0.04);
    color: #8b95c9;
    cursor: pointer;
    font-size: 0.9em;
    transition: all 0.2s;
  }}
  .page-btn:hover:not(:disabled) {{
    border-color: #60a5fa;
    color: #60a5fa;
  }}
  .page-btn:disabled {{
    opacity: 0.3;
    cursor: not-allowed;
  }}
  /* 详情弹窗 */
  .detail-overlay {{
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(5,7,20,0.85);
    z-index: 1000;
    justify-content: center;
    align-items: center;
    padding: 40px;
  }}
  .detail-overlay.open {{
    display: flex;
  }}
  .detail-panel {{
    background: #0f1335;
    border: 1px solid rgba(96,165,250,0.25);
    border-radius: 16px;
    max-width: 700px;
    width: 100%;
    max-height: 80vh;
    overflow-y: auto;
    padding: 32px;
    position: relative;
  }}
  .detail-close {{
    position: absolute;
    top: 16px; right: 16px;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.1);
    color: #8b95c9;
    width: 32px; height: 32px;
    border-radius: 8px;
    font-size: 18px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s;
  }}
  .detail-close:hover {{
    background: rgba(255,255,255,0.12);
    color: #e0e6ff;
  }}
  .detail-meta {{
    display: flex;
    gap: 10px;
    align-items: center;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }}
  .detail-title {{
    font-size: 1.3em;
    font-weight: 700;
    color: #e0e6ff;
    line-height: 1.4;
    margin-bottom: 16px;
  }}
  .detail-body {{
    color: #9ba4cc;
    font-size: 0.95em;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-word;
    margin-bottom: 16px;
  }}
  .detail-field {{
    margin-bottom: 12px;
  }}
  .detail-label {{
    color: #4a5280;
    font-size: 0.8em;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 4px;
  }}
  .detail-value {{
    color: #c4cbf5;
    font-size: 0.9em;
    word-break: break-all;
  }}
  .detail-tags {{
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 12px;
  }}
  .page-info {{
    color: #4a5280;
    font-size: 0.85em;
    min-width: 100px;
    text-align: center;
  }}

  /* Footer */
  .footer {{
    text-align: center;
    padding: 40px 20px;
    color: #4a5280;
    font-size: 0.85em;
    border-top: 1px solid rgba(255,255,255,0.05);
  }}
  .footer a {{ color: #60a5fa; text-decoration: none; }}

  @media (max-width: 768px) {{
    .chart-row {{ grid-template-columns: 1fr; }}
    .header h1 {{ font-size: 1.8em; }}
    .stats-grid {{ grid-template-columns: repeat(2, 1fr); }}
    .items-grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>

<div class="header">
  <h1>{title}</h1>
  <p class="subtitle">{stats['first_date']} ~ {stats['last_date']}</p>
</div>

<!-- 数字卡片 -->
<div class="stats-grid">
  <div class="stat-card">
    <div class="number">{stats['total']:,}</div>
    <div class="label">总收藏数</div>
  </div>
  <div class="stat-card">
    <div class="number">{stats['span_days']:,}</div>
    <div class="label">跨越天数</div>
  </div>
  <div class="stat-card">
    <div class="number">{stats['daily_avg']}</div>
    <div class="label">日均收藏</div>
  </div>
  <div class="stat-card">
    <div class="number">{len(stats['source_top'])}</div>
    <div class="label">不同来源</div>
  </div>
</div>

<div class="container">

  <!-- 亮点发现 -->
  <div class="highlights">
    <div class="highlight">
      <div class="hl-label">收藏最多的一天</div>
      <div class="hl-value">{stats['busiest_day']}</div>
      <div class="hl-detail">共收藏 {stats['busiest_count']} 条</div>
    </div>
    <div class="highlight">
      <div class="hl-label">最爱的来源</div>
      <div class="hl-value">{top_source}</div>
      <div class="hl-detail">贡献了 {top_source_count} 条收藏</div>
    </div>
    <div class="highlight">
      <div class="hl-label">最多的类型</div>
      <div class="hl-value">{top_type}</div>
      <div class="hl-detail">占比 {top_type_pct}%</div>
    </div>
  </div>

  <!-- 月度趋势 -->
  <div class="chart-full">
    <div class="chart-box">
      <h3>月度收藏趋势</h3>
      <div id="chart-monthly" class="chart"></div>
    </div>
  </div>

  <!-- 类型分布 + 来源排行 -->
  <div class="chart-row">
    <div class="chart-box">
      <h3>内容类型分布</h3>
      <div id="chart-type" class="chart"></div>
    </div>
    <div class="chart-box">
      <h3>来源 Top 15</h3>
      <div id="chart-source" class="chart chart-tall"></div>
    </div>
  </div>

  <!-- 时间热力图 -->
  <div class="chart-full">
    <div class="chart-box">
      <h3>收藏时间热力图（星期 x 小时）</h3>
      <div id="chart-heatmap" class="chart"></div>
    </div>
  </div>

  <!-- 小时分布 + 星期分布 -->
  <div class="chart-row">
    <div class="chart-box">
      <h3>每小时收藏分布</h3>
      <div id="chart-hour" class="chart"></div>
    </div>
    <div class="chart-box">
      <h3>每周收藏分布</h3>
      <div id="chart-weekday" class="chart"></div>
    </div>
  </div>

  <!-- 关键词云 -->
  <div class="chart-full">
    <div class="chart-box">
      <h3>关键词云</h3>
      <div id="chart-wordcloud" class="chart chart-tall"></div>
    </div>
  </div>

  <!-- 标签分布（如有） -->
  {"" if not has_tags else '''
  <div class="chart-full">
    <div class="chart-box">
      <h3>标签分布</h3>
      <div id="chart-tags" class="chart"></div>
    </div>
  </div>
  '''}

</div>

<!-- 分类浏览区 -->
<div class="browse-section" id="browse">
  <div class="browse-header">
    <h2>收藏浏览</h2>
  </div>
  <div class="filter-group">
    <span class="filter-label">按类型</span>
    <div class="tab-bar" id="type-tabs"></div>
  </div>
  <div class="filter-group" id="tag-group">
    <span class="filter-label">按标签</span>
    <div class="tab-bar" id="tag-tabs"></div>
  </div>
  <div class="search-row">
    <input type="text" class="search-input" id="search-input"
           placeholder="搜索标题、描述、来源...">
    <select class="sort-select" id="sort-select">
      <option value="time-desc">最新优先</option>
      <option value="time-asc">最早优先</option>
      <option value="source">按来源</option>
    </select>
  </div>
  <div class="browse-info" id="browse-info"></div>
  <div class="items-grid" id="items-grid"></div>
  <div class="pagination" id="pagination"></div>
</div>

<!-- 详情弹窗 -->
<div class="detail-overlay" id="detail-overlay">
  <div class="detail-panel" id="detail-panel">
    <button class="detail-close" id="detail-close">&times;</button>
    <div id="detail-content"></div>
  </div>
</div>

<div class="footer">
  Generated by <a href="#">wechat-local-service-kit</a>
</div>

<script>
const darkTheme = {{
  backgroundColor: 'transparent',
  textStyle: {{ color: '#8b95c9' }},
  title: {{ textStyle: {{ color: '#c4cbf5' }} }},
  legend: {{ textStyle: {{ color: '#8b95c9' }} }},
}};

// 月度趋势
(function() {{
  const chart = echarts.init(document.getElementById('chart-monthly'));
  const data = {monthly_json};
  chart.setOption({{
    ...darkTheme,
    tooltip: {{ trigger: 'axis' }},
    xAxis: {{
      type: 'category',
      data: data.map(d => d.month),
      axisLabel: {{ color: '#6b7394', rotate: 45 }},
      axisLine: {{ lineStyle: {{ color: '#2a2f5a' }} }},
    }},
    yAxis: {{
      type: 'value',
      axisLabel: {{ color: '#6b7394' }},
      splitLine: {{ lineStyle: {{ color: '#1a1f4e' }} }},
    }},
    series: [{{
      type: 'line',
      data: data.map(d => d.count),
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: {{ color: '#60a5fa', width: 3 }},
      itemStyle: {{ color: '#60a5fa' }},
      areaStyle: {{
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          {{ offset: 0, color: 'rgba(96,165,250,0.3)' }},
          {{ offset: 1, color: 'rgba(96,165,250,0.02)' }},
        ])
      }},
    }}],
    grid: {{ left: 50, right: 20, bottom: 60, top: 20 }},
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// 类型分布
(function() {{
  const chart = echarts.init(document.getElementById('chart-type'));
  const data = {type_dist_json};
  const colors = ['#60a5fa', '#a78bfa', '#f472b6', '#34d399', '#fbbf24', '#fb923c', '#f87171', '#818cf8'];
  chart.setOption({{
    ...darkTheme,
    tooltip: {{ trigger: 'item', formatter: '{{b}}: {{c}} ({{d}}%)' }},
    series: [{{
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['50%', '50%'],
      data: data,
      label: {{
        color: '#8b95c9',
        formatter: '{{b}}\\n{{d}}%',
      }},
      itemStyle: {{
        borderColor: '#0a0e27',
        borderWidth: 3,
      }},
      color: colors,
    }}],
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// 来源排行
(function() {{
  const chart = echarts.init(document.getElementById('chart-source'));
  const data = {source_top_json};
  const reversed = data.slice().reverse();
  chart.setOption({{
    ...darkTheme,
    tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }} }},
    xAxis: {{
      type: 'value',
      axisLabel: {{ color: '#6b7394' }},
      splitLine: {{ lineStyle: {{ color: '#1a1f4e' }} }},
    }},
    yAxis: {{
      type: 'category',
      data: reversed.map(d => {{
        const name = d.name;
        return name.length > 12 ? name.slice(0, 12) + '...' : name;
      }}),
      axisLabel: {{ color: '#8b95c9', fontSize: 11 }},
      axisLine: {{ lineStyle: {{ color: '#2a2f5a' }} }},
    }},
    series: [{{
      type: 'bar',
      data: reversed.map(d => d.value),
      itemStyle: {{
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
          {{ offset: 0, color: '#60a5fa' }},
          {{ offset: 1, color: '#a78bfa' }},
        ]),
        borderRadius: [0, 4, 4, 0],
      }},
      barWidth: '60%',
    }}],
    grid: {{ left: 120, right: 30, bottom: 20, top: 10 }},
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// 热力图
(function() {{
  const chart = echarts.init(document.getElementById('chart-heatmap'));
  const data = {heatmap_json};
  const hours = Array.from({{length:24}}, (_, i) => i + ':00');
  const days = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
  const maxVal = Math.max(...data.map(d => d[2]), 1);
  chart.setOption({{
    ...darkTheme,
    tooltip: {{
      formatter: function(p) {{
        return days[p.data[1]] + ' ' + hours[p.data[0]] + ': ' + p.data[2] + '条';
      }}
    }},
    xAxis: {{
      type: 'category',
      data: hours,
      axisLabel: {{ color: '#6b7394', fontSize: 10 }},
      splitArea: {{ show: false }},
    }},
    yAxis: {{
      type: 'category',
      data: days,
      axisLabel: {{ color: '#8b95c9' }},
    }},
    visualMap: {{
      min: 0,
      max: maxVal,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      inRange: {{
        color: ['#0a0e27', '#1e3a5f', '#3b82f6', '#60a5fa', '#93c5fd']
      }},
      textStyle: {{ color: '#6b7394' }},
    }},
    series: [{{
      type: 'heatmap',
      data: data,
      label: {{ show: false }},
      itemStyle: {{ borderColor: '#0a0e27', borderWidth: 2 }},
    }}],
    grid: {{ left: 60, right: 20, bottom: 60, top: 10 }},
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// 每小时分布
(function() {{
  const chart = echarts.init(document.getElementById('chart-hour'));
  const data = {hour_json};
  chart.setOption({{
    ...darkTheme,
    tooltip: {{ trigger: 'axis' }},
    xAxis: {{
      type: 'category',
      data: data.map(d => d.hour + ':00'),
      axisLabel: {{ color: '#6b7394', rotate: 45, fontSize: 10 }},
      axisLine: {{ lineStyle: {{ color: '#2a2f5a' }} }},
    }},
    yAxis: {{
      type: 'value',
      axisLabel: {{ color: '#6b7394' }},
      splitLine: {{ lineStyle: {{ color: '#1a1f4e' }} }},
    }},
    series: [{{
      type: 'bar',
      data: data.map(d => d.count),
      itemStyle: {{
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          {{ offset: 0, color: '#a78bfa' }},
          {{ offset: 1, color: 'rgba(167,139,250,0.2)' }},
        ]),
        borderRadius: [4, 4, 0, 0],
      }},
    }}],
    grid: {{ left: 40, right: 10, bottom: 50, top: 10 }},
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// 星期分布
(function() {{
  const chart = echarts.init(document.getElementById('chart-weekday'));
  const data = {weekday_json};
  chart.setOption({{
    ...darkTheme,
    tooltip: {{ trigger: 'axis' }},
    xAxis: {{
      type: 'category',
      data: data.map(d => d.day),
      axisLabel: {{ color: '#6b7394' }},
      axisLine: {{ lineStyle: {{ color: '#2a2f5a' }} }},
    }},
    yAxis: {{
      type: 'value',
      axisLabel: {{ color: '#6b7394' }},
      splitLine: {{ lineStyle: {{ color: '#1a1f4e' }} }},
    }},
    series: [{{
      type: 'bar',
      data: data.map(d => d.count),
      itemStyle: {{
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          {{ offset: 0, color: '#34d399' }},
          {{ offset: 1, color: 'rgba(52,211,153,0.2)' }},
        ]),
        borderRadius: [4, 4, 0, 0],
      }},
    }}],
    grid: {{ left: 40, right: 10, bottom: 30, top: 10 }},
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// 关键词云
(function() {{
  const chart = echarts.init(document.getElementById('chart-wordcloud'));
  const data = {keywords_json};
  if (data.length === 0) {{
    document.getElementById('chart-wordcloud').innerHTML =
      '<p style="text-align:center;color:#4a5280;padding-top:150px;">暂无关键词数据</p>';
    return;
  }}
  chart.setOption({{
    series: [{{
      type: 'wordCloud',
      shape: 'circle',
      sizeRange: [14, 60],
      rotationRange: [-30, 30],
      rotationStep: 15,
      gridSize: 8,
      drawOutOfBound: false,
      textStyle: {{
        fontFamily: 'sans-serif',
        fontWeight: 'bold',
        color: function() {{
          const colors = ['#60a5fa','#a78bfa','#f472b6','#34d399','#fbbf24','#818cf8','#fb923c','#38bdf8'];
          return colors[Math.floor(Math.random() * colors.length)];
        }},
      }},
      data: data,
      width: '90%',
      height: '90%',
    }}],
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// 标签分布（如有）
(function() {{
  const el = document.getElementById('chart-tags');
  if (!el) return;
  const data = {tag_json};
  if (data.length === 0) {{
    el.innerHTML = '<p style="text-align:center;color:#4a5280;padding-top:150px;">暂无标签数据</p>';
    return;
  }}
  const chart = echarts.init(el);
  chart.setOption({{
    ...darkTheme,
    series: [{{
      type: 'wordCloud',
      shape: 'diamond',
      sizeRange: [16, 50],
      rotationRange: [0, 0],
      gridSize: 12,
      textStyle: {{
        fontWeight: 'bold',
        color: function() {{
          const colors = ['#60a5fa','#a78bfa','#f472b6','#34d399','#fbbf24'];
          return colors[Math.floor(Math.random() * colors.length)];
        }},
      }},
      data: data,
    }}],
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// ===== 分类浏览区 =====
(function() {{
  const allItems = {items_json};
  if (allItems.length === 0) return;

  const PAGE_SIZE = 24;
  let currentType = '全部';
  let currentTag = '全部';
  let currentSearch = '';
  let currentSort = 'time-desc';
  let currentPage = 1;

  const badgeClassMap = {{
    '文章/链接': 'badge-article',
    '图片': 'badge-image',
    '视频': 'badge-video',
    '文本': 'badge-text',
    '文件': 'badge-file',
    '笔记': 'badge-note',
    '聊天记录': 'badge-chat',
    '语音': 'badge-other',
    '小程序': 'badge-other',
    '位置': 'badge-other',
    '其他': 'badge-other',
  }};

  // 统计各类型数量
  const typeCounts = {{}};
  allItems.forEach(it => {{
    typeCounts[it.type_name] = (typeCounts[it.type_name] || 0) + 1;
  }});
  const typeOrder = Object.entries(typeCounts).sort((a,b) => b[1] - a[1]);

  // 渲染 tabs（用 event delegation 代替 inline onclick，兼容 file:// 协议）
  function renderTabs() {{
    const tabBar = document.getElementById('type-tabs');
    let html = `<button class="tab-btn ${{currentType === '全部' ? 'active' : ''}}"
                  data-type="全部">全部<span class="tab-count">${{allItems.length}}</span></button>`;
    typeOrder.forEach(([name, count]) => {{
      html += `<button class="tab-btn ${{currentType === name ? 'active' : ''}}"
                 data-type="${{name}}">
                 ${{name}}<span class="tab-count">${{count}}</span></button>`;
    }});
    tabBar.innerHTML = html;
  }}
  document.getElementById('type-tabs').addEventListener('click', function(e) {{
    const btn = e.target.closest('.tab-btn');
    if (btn && btn.dataset.type) {{
      window._browseSetType(btn.dataset.type);
    }}
  }});

  // 标签统计
  const tagCounts = {{}};
  allItems.forEach(function(it) {{
    (it.tags || []).forEach(function(t) {{
      tagCounts[t] = (tagCounts[t] || 0) + 1;
    }});
  }});
  const tagOrder = Object.entries(tagCounts).sort(function(a,b) {{ return b[1] - a[1]; }});
  const hasAnyTags = tagOrder.length > 0;

  function renderTagTabs() {{
    var tagGroup = document.getElementById('tag-group');
    var tagBar = document.getElementById('tag-tabs');
    if (!hasAnyTags) {{ tagGroup.style.display = 'none'; return; }}
    var html = '<button class="tab-btn ' + (currentTag === '全部' ? 'active' : '') + '" data-tag="全部">全部</button>';
    tagOrder.forEach(function(pair) {{
      var name = pair[0], count = pair[1];
      html += '<button class="tab-btn ' + (currentTag === name ? 'active' : '') + '" data-tag="' + name.replace(/"/g, '&quot;') + '">' + name + '<span class="tab-count">' + count + '</span></button>';
    }});
    tagBar.innerHTML = html;
  }}
  document.getElementById('tag-tabs').addEventListener('click', function(e) {{
    var btn = e.target.closest('.tab-btn');
    if (btn && btn.dataset.tag !== undefined) {{
      currentTag = btn.dataset.tag;
      currentPage = 1;
      renderTagTabs();
      renderItems();
    }}
  }});

  // 过滤 + 排序
  function getFiltered() {{
    let items = allItems;
    if (currentType !== '全部') {{
      items = items.filter(it => it.type_name === currentType);
    }}
    if (currentTag !== '全部') {{
      items = items.filter(function(it) {{ return (it.tags || []).indexOf(currentTag) >= 0; }});
    }}
    if (currentSearch) {{
      const q = currentSearch.toLowerCase();
      items = items.filter(it =>
        (it.title || '').toLowerCase().includes(q) ||
        (it.desc || '').toLowerCase().includes(q) ||
        (it.source || '').toLowerCase().includes(q) ||
        (it.tags || []).some(t => t.toLowerCase().includes(q))
      );
    }}
    if (currentSort === 'time-desc') {{
      items.sort((a,b) => (b.timestamp || '').localeCompare(a.timestamp || ''));
    }} else if (currentSort === 'time-asc') {{
      items.sort((a,b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
    }} else if (currentSort === 'source') {{
      items.sort((a,b) => (a.source || '').localeCompare(b.source || ''));
    }}
    return items;
  }}

  // 渲染卡片
  function renderItems() {{
    const filtered = getFiltered();
    const total = filtered.length;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * PAGE_SIZE;
    const pageItems = filtered.slice(start, start + PAGE_SIZE);

    // info
    document.getElementById('browse-info').textContent =
      `共 ${{total}} 条${{currentType !== '全部' ? '（' + currentType + '）' : ''}}${{currentSearch ? '，搜索: "' + currentSearch + '"' : ''}}`;

    // cards
    const grid = document.getElementById('items-grid');
    if (pageItems.length === 0) {{
      grid.innerHTML = '<p style="text-align:center;color:#4a5280;padding:60px 0;grid-column:1/-1;">没有匹配的收藏</p>';
    }} else {{
      grid.innerHTML = pageItems.map(it => {{
        const badge = badgeClassMap[it.type_name] || 'badge-other';
        const date = it.timestamp ? it.timestamp.slice(0, 10) : '';
        const title = it.title || '(无标题)';
        const desc = it.desc || '';
        const tags = (it.tags || []).map(t => `<span class="item-tag">${{t}}</span>`).join('');
        const source = it.source ? `<span class="item-source">${{it.source}}</span>` : '';
        var thumbHtml = it.thumb ? '<img src="' + it.thumb + '" style="width:100%;max-height:160px;object-fit:cover;border-radius:8px;margin-bottom:8px;" loading="lazy" onerror="this.style.display=&quot;none&quot;">' : '';
        return `<div class="item-card" data-id="${{it.id}}">
          <div class="item-top">
            <span class="item-type-badge ${{badge}}">${{it.type_name}}</span>
            <span class="item-date">${{date}}</span>
          </div>
          ${{thumbHtml}}
          <div class="item-title">${{title}}</div>
          ${{desc ? `<div class="item-desc">${{desc}}</div>` : ''}}
          <div class="item-bottom">${{source}}${{tags}}</div>
        </div>`;
      }}).join('');
    }}

    // pagination
    const pag = document.getElementById('pagination');
    if (totalPages <= 1) {{
      pag.innerHTML = '';
    }} else {{
      pag.innerHTML = `
        <button class="page-btn" ${{currentPage <= 1 ? 'disabled' : ''}}
                data-page="${{currentPage - 1}}">上一页</button>
        <span class="page-info">${{currentPage}} / ${{totalPages}}</span>
        <button class="page-btn" ${{currentPage >= totalPages ? 'disabled' : ''}}
                data-page="${{currentPage + 1}}">下一页</button>
      `;
    }}
  }}

  // 全局回调
  window._browseSetType = function(type) {{
    currentType = type;
    currentPage = 1;
    renderTabs();
    renderItems();
  }};
  window._browsePage = function(page) {{
    currentPage = page;
    renderItems();
    document.getElementById('browse').scrollIntoView({{ behavior: 'smooth' }});
  }};

  // 翻页事件委托
  document.getElementById('pagination').addEventListener('click', function(e) {{
    const btn = e.target.closest('.page-btn');
    if (btn && btn.dataset.page && !btn.disabled) {{
      window._browsePage(parseInt(btn.dataset.page));
    }}
  }});

  // 搜索
  document.getElementById('search-input').addEventListener('input', function(e) {{
    currentSearch = e.target.value.trim();
    currentPage = 1;
    renderItems();
  }});

  // 排序
  document.getElementById('sort-select').addEventListener('change', function(e) {{
    currentSort = e.target.value;
    currentPage = 1;
    renderItems();
  }});

  // 详情弹窗
  function escHtml(s) {{
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }}

  function showDetail(itemId) {{
    var item = allItems.find(function(it) {{ return it.id === itemId; }});
    if (!item) return;
    var badge = badgeClassMap[item.type_name] || 'badge-other';
    var tags = (item.tags || []).map(function(t) {{
      return '<span class="item-tag">' + escHtml(t) + '</span>';
    }}).join('');

    var html = '<div class="detail-meta">' +
      '<span class="item-type-badge ' + badge + '">' + escHtml(item.type_name) + '</span>' +
      '<span class="item-date">' + escHtml(item.timestamp ? item.timestamp.slice(0, 19).replace('T', ' ') : '') + '</span>' +
      '</div>' +
      '<div class="detail-title">' + escHtml(item.title || '(无标题)') + '</div>';

    if (item.thumb) {{
      html += '<img src="' + escHtml(item.thumb) + '" style="width:100%;max-height:300px;object-fit:contain;border-radius:8px;margin-bottom:16px;background:#1a1f4e;" onerror="this.style.display=&quot;none&quot;">';
    }}

    var mediaTypes = ['图片', '视频', '文件', '语音'];
    if (mediaTypes.indexOf(item.type_name) >= 0 && !item.thumb) {{
      html += '<div style="padding:20px;text-align:center;background:rgba(96,165,250,0.06);border:1px dashed rgba(96,165,250,0.2);border-radius:8px;margin-bottom:16px;color:#4a5280;font-size:0.85em;">' +
        item.type_name + '内容存储在微信 CDN，本地缓存已加密，暂无法直接预览</div>';
    }}

    if (item.desc) {{
      html += '<div class="detail-body">' + escHtml(item.desc) + '</div>';
    }}

    if (item.link) {{
      html += '<div class="detail-field">' +
        '<div class="detail-label">链接</div>' +
        '<div class="detail-value"><a href="' + escHtml(item.link) + '" target="_blank" style="color:#60a5fa;word-break:break-all;">' + escHtml(item.link.length > 80 ? item.link.slice(0,80) + '...' : item.link) + '</a></div>' +
        '</div>';
    }}

    if (item.source) {{
      html += '<div class="detail-field">' +
        '<div class="detail-label">来源</div>' +
        '<div class="detail-value">' + escHtml(item.source) + '</div>' +
        '</div>';
    }}

    if (tags) {{
      html += '<div class="detail-field">' +
        '<div class="detail-label">标签</div>' +
        '<div class="detail-tags">' + tags + '</div>' +
        '</div>';
    }}

    document.getElementById('detail-content').innerHTML = html;
    document.getElementById('detail-overlay').classList.add('open');
  }}

  // 卡片点击（事件委托）
  document.getElementById('items-grid').addEventListener('click', function(e) {{
    var card = e.target.closest('.item-card');
    if (card && card.dataset.id) {{
      showDetail(parseInt(card.dataset.id));
    }}
  }});

  // 关闭弹窗
  document.getElementById('detail-close').addEventListener('click', function() {{
    document.getElementById('detail-overlay').classList.remove('open');
  }});
  document.getElementById('detail-overlay').addEventListener('click', function(e) {{
    if (e.target === this) {{
      this.classList.remove('open');
    }}
  }});
  document.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape') {{
      document.getElementById('detail-overlay').classList.remove('open');
    }}
  }});

  renderTabs();
  renderTagTabs();
  renderItems();
}})();
</script>

</body>
</html>"""
    return html


def main():
    parser = argparse.ArgumentParser(description="生成微信收藏可视化 HTML 报告")
    parser.add_argument("--input", "-i", required=True, help="parse_favorites.py 输出的 JSON 文件")
    parser.add_argument("--output", "-o", required=True, help="输出 HTML 文件路径")
    parser.add_argument("--title", "-t", default="我的微信收藏报告", help="报告标题")
    args = parser.parse_args()

    input_path = os.path.expanduser(args.input)
    output_path = os.path.expanduser(args.output)

    if not os.path.exists(input_path):
        print(f"[ERROR] 输入文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    stats = data.get("stats", {})
    if stats.get("total", 0) == 0:
        print("[ERROR] 没有收藏数据，无法生成报告", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] 正在生成报告，共 {stats['total']} 条收藏...")

    items = data.get("items", [])
    html = generate_html(stats, items=items, title=args.title)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[OK] 报告已生成: {output_path}")
    print(f"[INFO] 用浏览器打开查看: open \"{output_path}\"")


if __name__ == "__main__":
    main()
