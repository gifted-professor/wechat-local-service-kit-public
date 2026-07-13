# 微信聊天记录导出执行手册

这份手册基于一次本地跑通的流程整理，目标是把 macOS 微信本地聊天记录完整导出为结构化文件。

适用范围：
- macOS 微信 4.x
- 本地已有 `contact/session/message_*.db`
- 需要用 Frida 抓 PBKDF2 派生 key，再导出聊天记录

## 1. 当前结论

这套链路已经在本机验证通过。下方命令使用 `<wxid>` 作为占位符，请替换成你自己的本地账号目录。

关键结论：
- 日常登录请用原版微信：`/Applications/WeChat.app`
- 调试版微信只在“抓 key / Frida 调试”时临时使用
- 聊天库不是所有 DB 共用一个 `enc_key`
- 推荐直接使用 `--frida-log /tmp/wechat_frida_keys.log` 自动按每个 DB 的 salt 匹配 key

## 2. 目录和产物

本地聊天库目录通常类似：

```bash
$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage
```

导出后的主要文件：
- `out/chat-export/export/manifest.json`：总览统计
- `out/chat-export/export/conversation_index.json`：会话索引
- `out/chat-export/export/sessions.json`：会话元数据
- `out/chat-export/export/contacts.json`：联系人信息
- `out/chat-export/export/messages_summary.csv`：会话汇总表
- `out/chat-export/export/conversations/*.jsonl`：逐条聊天记录

每个 `jsonl` 文件里是一行一条消息，常见字段包括：
- `timestamp`
- `conversation_username`
- `conversation_name`
- `sender_id`
- `sender_name`
- `direction`
- `message_type`
- `render_type`
- `text`
- `attachment_meta`
- `source_db`

## 3. 日常使用建议

推荐工作方式：
- 平时一直使用原版微信
- 只有在需要重新抓 key 时，才临时打开调试版微信

不建议长期登录调试版的原因：
- 它只是为了让 Frida 能正常 `spawn + attach`
- 是重签后的临时副本，不适合长期当主力客户端
- 原版微信更新后，调试版通常也需要重新复制和重签

## 4. 一次性准备

### 4.1 Python 依赖

```bash
pip3 install frida frida-tools pycryptodome
```

### 4.2 Developer mode 和 `_developer`

如果这台机器还没配过，先执行：

```bash
sudo dseditgroup -o edit -a "$USER" -t user _developer
sudo /usr/sbin/DevToolsSecurity -enable
```

然后：
- 完全退出 `Terminal.app` / `iTerm.app` / 当前脚本宿主 App
- 重新打开宿主 App
- 到“系统设置 > 隐私与安全性 > 开发者工具”里勾选当前实际运行命令的宿主 App

注意：
- 如果你在 Terminal/iTerm 里执行脚本，就要给对应终端权限
- 如果你在其他宿主 App 里执行脚本，就要给那个宿主 App 权限

### 4.3 创建调试版微信副本

不要直接在 `/Applications/WeChat.app` 上动手。推荐放到 `~/.wx-debug/`：

```bash
python3 scripts/setup_wechat_debug_app.py
```

调试版可执行文件路径：

```bash
$HOME/.wx-debug/WeChat-debug.app/Contents/MacOS/WeChat
```

## 5. 标准执行流程

推荐先使用 bootstrap 入口跑完整链路：

```bash
python3 scripts/bootstrap_local_wechat.py --doctor-only
python3 scripts/bootstrap_local_wechat.py --run --with-memory
```

如果检测到多个本地微信账号，显式指定账号目录：

```bash
python3 scripts/bootstrap_local_wechat.py \
  --run \
  --with-memory \
  --wechat-root "$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage"
```

下面是同一流程的手动拆解版本。

### Step 1. 检查 Frida 权限

```bash
python3 scripts/test_frida_attach.py
```

通过标准：
- 输出里至少出现一个 `ok`
- 最理想是 `attach_existing: ok` 和 `spawn_attach: ok`

如果需要直接打开设置页：

```bash
python3 scripts/test_frida_attach.py --open-settings
```

### Step 2. 退出原版微信

抓 key 前，先确保原版微信彻底退出。

```bash
killall WeChat 2>/dev/null || true
```

### Step 3. 用调试版微信抓 key

```bash
python3 scripts/grab_wechat_key.py \
  --app "$HOME/.wx-debug/WeChat-debug.app/Contents/MacOS/WeChat" \
  --wait 240
```

脚本运行后要做的事：
- 在调试版微信里登录账号
- 打开几个你确认有聊天记录的会话或客户聊天
- 等待命令结束

输出日志默认写到：

```bash
/tmp/wechat_frida_keys.log
```

判断是否抓到数据：
- 终端里出现多条 `[PBKDF2] rounds=... salt=...`
- `/tmp/wechat_frida_keys.log` 非空

### Step 4. 可选：验证某个 DB 的 key

如果你想单独验证某个库，比如 `message_0.db`：

```bash
python3 scripts/match_wechat_key.py \
  --db "$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage/message/message_0.db" \
  --log /tmp/wechat_frida_keys.log
```

这个脚本会输出：
- `validated`
- `matched_field`
- `key_hex`
- `key_mode`

说明：
- 这一步主要用于排错和确认
- 正常全量导出时，推荐直接走下一步，用 `--frida-log` 自动匹配每个 DB 的 key

### Step 5. 导出完整聊天记录

推荐命令：

```bash
python3 scripts/export_chat_history.py \
  --wechat-root "$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage" \
  --output "$PWD/out/chat-export" \
  --frida-log /tmp/wechat_frida_keys.log
```

说明：
- `--wechat-root` 可以传 `wxid/.../db_storage`
- 也可以传 `wxid/...`，脚本会自动定位 `db_storage`
- 推荐优先传 `--frida-log`，不要手工假设所有 DB 使用同一个 key

### Step 6. 按联系人或群过滤导出

如果只想导出某一个人或某一个群，可以加：

```bash
python3 scripts/export_chat_history.py \
  --wechat-root "$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage" \
  --output "$PWD/out/chat-export-single" \
  --frida-log /tmp/wechat_frida_keys.log \
  --conversation "客户名或群名"
```

`--conversation` 同时支持：
- 会话用户名
- 显示名

## 6. 如何查看聊天内容

### 6.1 先看会话索引

```bash
open out/chat-export/export/conversation_index.json
```

这个文件里会告诉你：
- 会话显示名
- 微信用户名
- 消息条数
- 对应的 `jsonl` 文件路径

### 6.2 看某个会话的逐条消息

例如：

```bash
head -n 5 out/chat-export/export/conversations/<conversation_id>.jsonl
```

每一行就是一条消息，包含文本、方向、发送人、时间等信息。

### 6.3 看总览统计

```bash
cat out/chat-export/export/manifest.json
```

`manifest.json` 会包含 `total_conversations`、`total_messages`、导出时间、过滤条件和输出文件信息。

## 7. 常见问题

### Q1. `test_frida_attach.py` 还是失败怎么办？

先看两件事：
- 当前命令到底是从 `Terminal.app`、`iTerm.app` 还是其他宿主 App 发起的
- 这个宿主 App 有没有在“开发者工具”里勾选，并且勾选后是否完全退出重开

只要 `spawn_attach: ok`，通常就够继续抓 key。

### Q2. 为什么不用原版微信直接抓？

原版微信通常带 Hardened Runtime，Frida 无法稳定 `spawn + attach`。
所以推荐只在抓 key 时使用重签的调试版副本。

### Q3. 为什么不建议长期登录调试版？

因为它只是临时调试工具，不是长期主力客户端。
日常使用请回到原版微信。

### Q4. 为什么只拿一个 `key_hex` 还会出现 `hmac mismatch`？

因为 `contact.db`、`session.db`、`message_*.db` 不一定共用同一个可直接解密的 key。
本仓库现在已经支持 `--frida-log`，会按每个 DB 的 salt 自动匹配正确 key。

### Q5. 什么时候需要重新抓 key？

常见场景：
- 微信更新后
- 换了账号
- 你怀疑日志里的 key 已经过期
- 重新复制了新的调试版微信

### Q6. 可以直接拿到聊天内容吗？

可以。
聊天内容已经在 `conversations/*.jsonl` 里，文字消息可直接读取，图片/语音/视频/位置等也会保留对应消息记录和一部分元数据。

## 8. 本仓库相关脚本

- `scripts/test_frida_attach.py`：检查本地 Frida 权限是否可用
- `scripts/frida_support.py`：Frida preflight、宿主链路、权限建议
- `scripts/grab_wechat_key.py`：hook `CCKeyDerivationPBKDF` 抓 PBKDF2 日志
- `scripts/match_wechat_key.py`：按 DB 或 salt 验证 key
- `scripts/chat_crypto.py`：SQLCipher 4 解密和 Frida log 自动匹配
- `scripts/parse_chat_history.py`：联系人、会话、消息解析
- `scripts/export_chat_history.py`：完整导出入口

## 9. 一条最短复用命令

如果这台机器环境已经配好，推荐直接跑：

```bash
python3 scripts/bootstrap_local_wechat.py --run --with-memory
```

如果你想手动拆开，最短是下面几步：

```bash
python3 scripts/grab_wechat_key.py \
  --app "$HOME/.wx-debug/WeChat-debug.app/Contents/MacOS/WeChat" \
  --wait 240

python3 scripts/export_chat_history.py \
  --wechat-root "$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage" \
  --output "$PWD/out/chat-export" \
  --frida-log /tmp/wechat_frida_keys.log
```

这就是当前已经验证通过的标准路径。
