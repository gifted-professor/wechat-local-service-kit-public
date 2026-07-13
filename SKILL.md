---
name: wechat-local-service-kit
description: |
  本地微信数据导出、收藏可视化、客户记忆和受监督客服回复草稿工具。
  触发词：/wechat-local-service-kit、微信本地数据、微信客服辅助、微信收藏可视化、收藏报告、收藏分析
  支持输入：加密/解密后的 Favorite.db（SQLite）或 CSV/JSON 导出文件
---

# WeChat Local Service Kit Skill

将微信收藏数据（加密 Favorite.db）端到端解密、解析、生成交互式 HTML 可视化报告。

## 执行流程

### Step 1: 确认数据源

询问用户是否已有解密后的 Favorite.db。

- **已有解密 DB** → 跳到 Step 3
- **未解密** → 进入 Step 2 密钥提取 + 解密

### Step 2: 密钥提取与数据库解密（macOS）

#### 2a. 准备签名副本
```bash
# 复制微信到桌面并去掉 Hardened Runtime
cp -R /Applications/WeChat.app ~/Desktop/WeChat.app
codesign --force --deep --sign - ~/Desktop/WeChat.app
```

#### 2b. 安装 frida
```bash
pip3 install frida frida-tools
```

#### 2c. 提取密钥
```bash
# 关闭原版微信，从桌面副本启动
killall WeChat 2>/dev/null; sleep 2

# 用 frida spawn 模式启动微信并 hook PBKDF2
PYTHONPATH=$(python3 -c "import site; print(site.getusersitepackages())") python3 << 'PYEOF'
import frida, time

JS_CODE = """
function buf2hex(buffer) {
    var a = new Uint8Array(buffer); var h = '';
    for (var i = 0; i < a.length; i++) h += ('0' + a[i].toString(16)).slice(-2);
    return h;
}
var found = false;
Process.enumerateModules().forEach(function(m) {
    if (found) return;
    m.enumerateExports().forEach(function(exp) {
        if (found) return;
        if (exp.name === "CCKeyDerivationPBKDF") {
            found = true;
            send("[*] Hook installed on " + m.name);
            Interceptor.attach(exp.address, {
                onEnter: function(args) {
                    this.pwLen = args[2].toInt32();
                    this.saltLen = args[4].toInt32();
                    this.rounds = args[6].toInt32();
                    this.pw = args[1]; this.salt = args[3];
                    this.dk = args[7]; this.dkLen = args[8].toInt32();
                },
                onLeave: function(retval) {
                    if (this.pwLen < 4 || this.pwLen > 256) return;
                    if (this.saltLen < 4 || this.saltLen > 64) return;
                    var saltHex = buf2hex(this.salt.readByteArray(this.saltLen));
                    var dkHex = buf2hex(this.dk.readByteArray(this.dkLen));
                    var pwHex = buf2hex(this.pw.readByteArray(this.pwLen));
                    send("[PBKDF2] r=" + this.rounds + " salt=" + saltHex);
                    var f = new File("/tmp/wechat_frida_keys.log", "a");
                    f.write("rounds=" + this.rounds + "\\npw=" + pwHex + "\\nsalt=" + saltHex + "\\ndk=" + dkHex + "\\n\\n");
                    f.flush(); f.close();
                }
            });
        }
    });
});
if (!found) send("[!] CCKeyDerivationPBKDF not found");
"""

device = frida.get_local_device()
pid = device.spawn(["/Users/" + __import__('os').getenv('USER') + "/Desktop/WeChat.app/Contents/MacOS/WeChat"])
session = device.attach(pid)
script = session.create_script(JS_CODE)
script.on("message", lambda msg, data: print(msg.get("payload", msg)))
script.load()
device.resume(pid)
print("WeChat running. Login → open Favorites → wait 60s...")
time.sleep(120)
session.detach()
print("Done. Check /tmp/wechat_frida_keys.log")
PYEOF
```

**重要**：微信启动后必须登录并打开「收藏」页面，触发 favorite.db 的密钥加载。

#### 2d. 匹配密钥并解密

解析 frida 日志，找到 favorite.db 对应的 enc_key（通过 salt 匹配），然后用 Python 解密：

```python
# 核心解密逻辑（内置在 parse_favorites.py 的上游）
import hashlib, hmac, struct
from Crypto.Cipher import AES

# enc_key = 从 frida 日志中 256000 轮 PBKDF2 输出匹配 DB salt 的 dk
# 逐页 AES-256-CBC 解密，HMAC-SHA512 校验
```

安装依赖：`pip3 install pycryptodome`

### Step 3: 解析数据

```bash
python3 scripts/parse_favorites.py \
  --input "<解密后的 favorite.db 路径>" \
  --output "<输出目录>/data.json"
```

兼容 WeChat 3.x (FavItems) 和 4.x (fav_db_item) 表结构。

### Step 4: 生成可视化报告

```bash
python3 scripts/generate_report.py \
  --input "<输出目录>/data.json" \
  --output "<输出目录>/report.html"
```

### Step 5: 预览

```bash
# 推荐用 http server（file:// 下部分交互可能受限）
cd <输出目录> && python3 -m http.server 8765 &
open http://localhost:8765/report.html
```

## 报告功能

| 区域 | 内容 |
|------|------|
| 统计卡片 | 总数、时间跨度、日均收藏、最忙日 |
| 亮点发现 | 最爱来源、主力类型 |
| 月度趋势 | 折线 + 面积图 |
| 类型分布 | 甜甜圈图 |
| 来源 Top15 | 水平柱状图 |
| 活跃热力图 | 星期 × 小时 |
| 时段分布 | 小时 / 星期柱状图 |
| 词云 | 标题 + 描述提取 |
| 标签云 | 微信收藏标签 |
| **收藏浏览** | 按类型筛选 + 按标签筛选 + 搜索 + 排序 + 分页 |
| **详情弹窗** | 点击卡片展开完整内容、链接、来源、标签 |

## 收藏类型

| Type | 含义 | 解析字段 |
|------|------|----------|
| 1 | 文本 | desc |
| 2 | 图片 | datafmt + fullsize |
| 4 | 视频 | datatitle + datadesc |
| 5 | 文章/链接 | pagetitle + pagedesc + link + pagethumb_url |
| 8 | 文件 | datatitle + datafmt + fullsize |
| 14 | 聊天记录 | datalist → 逐条 datadesc 拼接 |
| 其他 | 位置/笔记/小程序等 | 通用 title + desc 回退 |

## 技术依赖

- Python 3.9+ 标准库（sqlite3, json, re, html, collections, datetime）
- PyCryptodome（仅解密步骤需要：`pip3 install pycryptodome`）
- frida（仅密钥提取步骤需要：`pip3 install frida frida-tools`）
- ECharts 5.x + echarts-wordcloud 2.x（CDN 内联，无需安装）

## 输出文件

```
<输出目录>/
├── data.json      # 解析后的结构化数据
└── report.html    # 单文件可视化报告（可直接分享）
```

## 已知限制

- 图片/视频/文件的原始内容存储在微信 CDN（加密内部格式），本地缓存也加密，无法在报告中直接预览
- 文章缩略图来自微信公众号 CDN，需联网加载
- 密钥提取需要 macOS，frida 需要 ad-hoc 签名的微信副本
