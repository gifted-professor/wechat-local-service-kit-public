# 微信收藏可视化：架构与踩坑记

## 整体架构

```
微信 Mac 客户端 → 加密 favorite.db (SQLCipher 4)
       ↓ frida hook CCKeyDerivationPBKDF
    密钥提取 → PBKDF2 派生 enc_key
       ↓ Python AES-256-CBC 解密
    明文 SQLite → XML 解析 (10种收藏类型)
       ↓ generate_report.py
    单文件 HTML 报告 (ECharts + 交互浏览)
```

## 技术栈

| 层级 | 技术 | 作用 |
|------|------|------|
| 密钥提取 | frida 17.9 + Python | Hook CCKeyDerivationPBKDF 捕获 256000 轮 PBKDF2 |
| 数据库解密 | PyCryptodome AES-256-CBC | 逐页解密 SQLCipher 4 (page_size=4096, reserve=80) |
| 数据解析 | Python sqlite3 + regex XML | 解析 fav_db_item 表的 XML content 字段 |
| 可视化 | ECharts 5.x CDN + 原生 JS | 7 种图表 + 分类浏览 + 标签筛选 + 详情弹窗 |

## 迭代路径 (6 轮)

### Round 1: 内存扫描 x'hex' 模式 → 失败
- 方案: C 编译 find_all_keys_macos.c，扫描 RW 内存区域找 x'<64hex><32hex>' 格式密钥
- 结果: 0 keys found
- 原因: WeChat 4.1.2 不用 hex 字符串格式存储密钥

### Round 2: 原始字节 salt 匹配 → 误匹配
- 方案: 在内存中搜索 DB 文件前 16 字节 (salt)，取前 32 字节作为密钥
- 结果: 找到的是 ASCII 字符串 ("matchinfo" + "optimize")，不是真正的密钥

### Round 3: HMAC 暴力验证 → 未找到
- 方案: 遍历内存每个 8 字节对齐位置，用 HMAC-SHA512 验证是否为正确密钥
- 结果: 扫描 4.2GB，89M 候选，耗时 347 秒，未找到
- 原因: 加了 ASCII/zero 过滤跳过了真正的密钥位置

### Round 4: 无过滤暴力 + 4 字节对齐 → 仍未找到
- 方案: 去掉所有过滤条件，先 8 字节对齐再 4 字节对齐
- 结果: 215M 候选，817 秒，仍未找到
- 根本原因: 密钥经过 256000 轮 PBKDF2 派生后存储在内存中，不是原始 enc_key

### Round 5: DYLD hook / lldb attach → 被阻止
- DYLD_INSERT_LIBRARIES: macOS 阻止注入，captured_keys.log 为空
- lldb attach: "Not allowed to attach to process"
- task_for_pid (原版微信): 返回错误码 5，SIP 保护

### Round 6: frida spawn + CCKeyDerivationPBKDF → 成功!
- 关键突破: 复制微信到 ~/Desktop，ad-hoc 签名去掉 Hardened Runtime
- frida spawn 模式启动微信，hook 系统库 CCKeyDerivationPBKDF
- 捕获所有 PBKDF2 调用，匹配 favorite.db 的 salt (`<redacted_salt_prefix>...`)
- 获得 enc_key: `<redacted_64_hex_key>`

## 关键踩坑

### 坑 1: SIP 阻止签名 /Applications 下的微信
- 解决: cp -R 到 ~/Desktop，在那里 codesign

### 坑 2: sudo 启动微信 → 数据目录变成 /var/root
- 之前用 sudo + frida spawn，微信以 root 运行，访问不到用户的收藏数据
- 解决: 不用 sudo，直接以用户身份运行 frida

### 坑 3: frida attach 模式卡住 / 崩溃
- DebugSymbol.fromName 在某些情况下无限等待
- Module.findExportByName 返回 null (符号被剥除)
- 解决: 用 enumerateModules + enumerateExports 手动查找

### 坑 4: favorite.db 的密钥在启动时未加载
- 第一次 frida 捕获的密钥不包含 favorite.db 的 salt
- 原因: 微信启动时不打开收藏数据库，需要用户访问收藏页面
- 解决: frida 运行期间手动打开微信收藏

### 坑 5: WeChat 4.x 表结构变化
- 3.x: FavItems + FavDataItem 表
- 4.x: fav_db_item 单表，content 字段为 XML
- 解决: 检测表名自动适配

### 坑 6: XML 内容解析不完整
- 文章: <weburlitem><pagetitle> 而非 <title>
- 聊天记录: <datalist><dataitem><datadesc> 多条消息拼接
- 视频: <datatitle> + <datadesc> 在 dataitem 内
- &#x0A; 等 HTML 实体未解码
- 解决: 按类型分别提取 + html.unescape

### 坑 7: JS 模板字符串引号冲突
- onerror="this.style.display='none'" 在 Python f-string → JS 模板字符串中引号嵌套错误
- 导致整个浏览区 JS 静默失败
- 解决: 用 &quot; 替代嵌套引号

### 坑 8: file:// 协议下 inline onclick 不工作
- 解决: 改用 event delegation (addEventListener on parent)

## 数据统计

- 1988 条收藏记录
- 63 个标签
- 时间跨度: 2014-2026 (12 年)
- 类型分布: 文章 1646, 聊天记录 270, 文本 55, 文件 7, 其他 5, 图片 4, 视频 1
- 1568/1988 条有缩略图 (来自微信公众号 CDN)
