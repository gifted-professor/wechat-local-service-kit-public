# wechat-local-service-kit

[English](README.md) | **简体中文**

一个本地优先的 macOS 微信工具箱，用于导出微信数据、构建客户记忆，并生成由人工监督的客服回复草稿。

这个仓库最初用于生成微信收藏可视化报告，现在也包含一套更完整的本地微信助手流程：

- 将 macOS 微信加密数据库导出为结构化 JSONL。
- 按会话构建确定性的客户记忆档案。
- 生成便于人工检查的客户 Wiki 页面。
- 使用兼容 OpenAI API 的模型生成回复草稿，并可选择加入客户记忆。
- 真实消息发送始终位于本地 UI 自动化、dry-run 检查和人工明确确认之后。

项目坚持本地优先：微信数据库、提取出的密钥、聊天导出和客户记忆输出都保留在用户自己的电脑上，并由 Git 忽略。

## 单机与 Syncthing 的选择

如果微信和 Agent 运行在同一台 Mac 上，直接使用本地 `live-inbox`：

```text
微信 -> wx-cli/live-inbox worker -> ~/Sync/wechat-live-inbox/events.jsonl -> 本地 Agent 读取 JSONL
```

这种模式不需要 Syncthing。`live-inbox` 仍然有价值，因为 Agent 只需读取纯文本日志，不必接触微信数据库、密钥文件或微信界面。

只有另一台 Mac 需要读取 inbox 时才使用 Syncthing：

```text
生产端 Mac 写入 ~/Sync/wechat-live-inbox/events.jsonl
-> Syncthing
-> 读取端 Mac 使用同步后的 events.jsonl
```

建议 `live-inbox` 只保存文本事件。图片、音频、视频和数据库导出不应混入此目录，除非已经制定了明确的保留与清理策略。

## 新 Mac / 迁移快速开始

如果要把项目迁移到另一台 Mac，请先阅读：

- [MIGRATION.md](MIGRATION.md)：压缩包、GitHub clone、依赖、本地微信数据和仓库内 `wx-cli` profile 的迁移说明。
- 只 clone 代码不足以读取微信历史。目标 Mac 还需要本地微信数据、可用密钥，或重新执行一次密钥捕获。
- `out/` 和 `.wx-cli-*` 可能包含聊天导出和密钥，只能在同一所有者的可信设备之间私下迁移，绝不能提交到 GitHub。

如果把仓库交给朋友或安装 Agent，可以同时提供这段说明：

```text
Clone 仓库后，从 README.zh-CN.md 的“新 Mac / 迁移快速开始”开始操作。
不要打印 all_keys.json 或解密后的数据库内容。
遇到第一个权限或解密阻塞时停止，并返回准确命令和脱敏后的错误信息。
```

新 Mac 前提：

- 已安装 `/Applications/WeChat.app`，并登录目标微信账号。
- 已安装 Node.js 18+、Python 3.10+ 和 Git。
- 操作者可以在需要提取密钥时批准 macOS 本地权限提示。

Clone 并安装：

```bash
git clone https://github.com/gifted-professor/wechat-local-service-kit-public.git wechat-local-service-kit
cd wechat-local-service-kit
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
npm --prefix .wx-cli-tools ci
```

如果没有 `.wx-cli-tools/package-lock.json`，可以直接安装指定版本的 `wx-cli`：

```bash
mkdir -p .wx-cli-tools
npm --prefix .wx-cli-tools install @jackwener/wx-cli@0.1.10
```

项目使用的 `wx-cli` 来自 [`jackwener/wx-cli`](https://github.com/jackwener/wx-cli)。

在接触密钥之前先运行本地检查：

```bash
python3 scripts/bootstrap_local_wechat.py --doctor-only
python3 scripts/wechat_tool_status.py --skip-daemon --compact
```

首次提取密钥前需要先让微信加载代表性数据：

1. 保持微信打开并登录目标账号。
2. 打开至少两个私聊和一个群聊。
3. 在每个会话中向上滚动几屏历史消息。

当出现 `key_count = 0`、更换账号或重新绑定时，这是必要的人工步骤。已经稳定写入纯文本事件的 `live-inbox`，后续摘要和查询不需要重复此步骤。

初始化 `wx-cli` 并提取本地数据库密钥：

```bash
./.wx-cli-tools/node_modules/.bin/wx init --force
```

成功结果必须显示非零密钥数量，例如 `成功提取 N 个数据库密钥`，且 `N > 0`。

如果 macOS 阻止进程访问，可通过系统权限提示重新运行：

```bash
osascript -e 'do shell script "cd /path/to/wechat-local-service-kit && ./.wx-cli-tools/node_modules/.bin/wx init --force" with administrator privileges'
```

如果不确定目标账号目录，可以只列出本地账号文件夹，不读取任何密钥：

```bash
python3 - <<'PY'
from pathlib import Path

root = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
for path in sorted([p for p in root.glob("wxid_*") if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True):
    db = path / "db_storage"
    print(path.name, "db_storage=", db.exists(), "mtime=", int(path.stat().st_mtime))
PY
```

创建仓库内的 `wx-cli` profile。请把 `<wxid>` 替换为目标账号的本地目录名：

```bash
WXID="<wxid>"
mkdir -p ".wx-cli-$WXID"
cp "$HOME/.wx-cli/all_keys.json" ".wx-cli-$WXID/all_keys.json"
chmod 600 ".wx-cli-$WXID/all_keys.json"
cat > ".wx-cli-$WXID/config.json" <<EOF
{
  "decrypted_dir": "decrypted",
  "db_dir": "/Users/$USER/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/$WXID/db_storage",
  "keys_file": "all_keys.json"
}
EOF
printf '.wx-cli-%s\n' "$WXID" > .wx-cli-profile
```

不要复制、粘贴或打印 `all_keys.json`。它必须留在本机，不能进入 Git、聊天记录或截图。

在不暴露密钥的前提下验证 profile：

```bash
python3 scripts/manage_wechat_accounts.py list
python3 scripts/wechat_tool_status.py --skip-daemon --compact
```

被选中的 profile 应显示 `key_count > 0` 和 `has_db_dir: true`。随后执行小规模读取测试：

```bash
python3 scripts/export_wx_cli_history.py \
  --output "$PWD/out/wx-cli-smoke" \
  --private-only \
  --session-limit 5
```

测试通过后，可执行完整账号流程：

```bash
python3 scripts/export_chat_history.py \
  --output "out/accounts/<wxid>/chat-export" \
  --wx-cli-profile ".wx-cli-<wxid>"

python3 scripts/build_customer_memory.py \
  --export-root "out/accounts/<wxid>/chat-export/export" \
  --out-root "out/accounts/<wxid>/customer-memory"
```

常见首次安装问题：

- `key_count = 0`：微信尚未加载出可用密钥。保持微信打开，进入多个会话并滚动历史，然后再次运行 `wx init --force`。
- `task_for_pid 失败 (kr=5)` 或 attach/spawn 权限错误：这是 macOS 权限或签名问题，不是代码缺失。批准系统提示，使用上面的 `osascript` 命令，或参考 `docs/wechat-new-account-runbook.md`。
- `无法解密 session.db` 或 `file is not a database`：先检查当前 profile、`db_dir`、`key_count` 和 daemon/cache 是否对应同一账号。
- 输出来自错误账号：停止 daemon，确认 `.wx-cli-profile`，再从正确 profile 重新运行。
- GitHub clone 不包含 `out/`、`.wx-cli-profile`、`.wx-cli-*`、`all_keys.json`、解密数据库或客户记忆，这是有意的隐私边界。

完整排查步骤见 [微信新账号运行手册](docs/wechat-new-account-runbook.md)。

## 当前状态

仓库包含可运行的原型脚本，但还不是打包完成的产品。

已在本地验证：

- 基于 Frida 的 macOS 微信 4.x PBKDF2 密钥捕获。
- SQLCipher 风格的本地数据库处理。
- 联系人、会话与消息导出。
- 通过 `wx-cli` 读取会话、历史和新消息。
- 单会话 dry-run 回复生成。
- 客户记忆档案生成和 Markdown Wiki 渲染。
- 保守的记忆使用门控，避免在宽泛问题中误注入客户历史。

仍处于实验阶段：

- 长期运行的 daemon 打包。
- 对不同微信版本都可靠的 UI 发送验证。
- 多账号运行维护。
- 完全无人值守的自动回复。

## 安全模型

项目遵循严格的安全边界：

- 不提交私密数据、聊天导出、解密数据库或密钥文件。
- 没有操作者当下明确确认时，不发送真实微信消息。
- 开发和测试回复生成时优先使用 `--dry-run`。
- 把提取出的客户事实视为候选信息，而不是绝对事实。
- 除非人工为小范围测试明确开启，否则保持群聊自动回复关闭。

完整策略见 [安全与隐私](docs/security-and-privacy.md)。

## 仓库结构

```text
scripts/
  grab_wechat_key.py             # 使用 Frida 捕获 PBKDF2 事件
  match_wechat_key.py            # 根据数据库 salt 匹配捕获的密钥
  chat_crypto.py                 # 准备可读取的 SQLite 副本
  export_chat_history.py         # 导出联系人、会话和消息
  export_wx_cli_history.py       # 通过 wx-cli 导出相同结构的 JSONL
  parse_chat_history.py          # 解析微信消息数据库
  build_wechat_digest.py         # 从 live-inbox 或历史构建只读日报
  wx_cli_adapter.py              # 项目内 wx-cli 封装
  watch_conversation_messages.py # 监听一个会话的新消息
  auto_reply_once.py             # 执行一次受监督的回复周期
  wechat_reply_service.py        # 管理监听和仅草稿 worker
  customer_memory.py             # 构建、查询和渲染客户记忆
  build_customer_memory.py       # 构建确定性的客户档案
  query_customer_memory.py       # 查询客户记忆
  render_customer_pages.py       # 渲染可读的 Markdown 页面
  build_runtime_context.py       # 构建紧凑的提示词上下文
  compare_memory_draft.py        # 对比有无客户记忆的草稿
  compare_reply_contexts.py      # 对比不同知识上下文的草稿
  service_knowledge.py           # 从项目 Wiki 中选择服务话术
  setup_wechat_debug_app.py      # 创建 ad-hoc 签名的调试微信副本
  bootstrap_local_wechat.py      # 引导完成安装、导出和记忆构建

docs/
  chat-export-runbook.md
  wechat-new-account-runbook.md
  local-auto-reply-architecture.md
  wechat-digest-layer.md
  security-and-privacy.md
  references.md

.project-wiki/
  index.md
  wiki-schema.md
  wiki/architecture/
  wiki/reply-playbooks/
  wiki/operations/
  wiki/safety/
```

本地输出 `out/`、`decrypted/`、`.wx-cli-*`、密钥、配置、缓存和运行日志都由 Git 忽略。仓库只保留 `.wx-cli-tools/package.json` 与 lockfile，方便新 Mac 安装同一版本的工具链。

## 仓库内 wx-cli Profile

项目脚本可以使用仓库内的 active profile，而不是固定依赖 `~/.wx-cli`：

- 在 `.wx-cli-profile` 中写入相对或绝对 profile 路径。
- 可用 `WX_CLI_CONFIG_DIR=/path/to/profile` 临时覆盖。
- profile 仅保留在本地，并由 Git 忽略；典型目录包含 `config.json` 与 `all_keys.json`。

设置后，项目脚本会自动：

- 从对应 profile 目录运行 `wx` 命令。
- 切换账号时重启绑定错误的 `wx-daemon`。
- 让 `scripts/export_chat_history.py` 复用 profile 的 `db_dir` 与数据库密钥。

如果 profile 可以通过 `wx-cli` 读取，但还没有稳定的逐库密钥，仍可导出兼容 JSONL：

```bash
python3 scripts/export_wx_cli_history.py \
  --output "$PWD/out/wx-cli-export" \
  --private-only \
  --session-limit 10000
```

输出的 `out/wx-cli-export/export/` 可直接交给 `scripts/build_customer_memory.py`。

## 只读每日摘要

以下命令生成轻量日报，不调用 LLM、不操作微信，也不发送消息：

```bash
python3 scripts/build_wechat_digest.py \
  --source live-inbox \
  --live-inbox-root "$HOME/Sync/wechat-live-inbox" \
  --date "$(date +%Y-%m-%d)"
```

单机时指向本地 inbox；远程 Hermes/Codex Mac 则指向 Syncthing 同步后的副本。两种模式都只读取 `events.jsonl`。

结果写入 `out/wechat-digest/` 下的 `digest.json`、`digest.md` 和 `messages.jsonl`。详见 [微信摘要层](docs/wechat-digest-layer.md)。

## 多账号流程

多个账号可以保留各自独立的 profile：

- 每个账号使用 `.wx-cli-wxid_.../` 目录。
- `.wx-cli-profile` 指向当前账号。
- `scripts/manage_wechat_accounts.py` 可列出、切换账号，并同步账号专属的导出与客户记忆。

```bash
python3 scripts/manage_wechat_accounts.py list
python3 scripts/manage_wechat_accounts.py activate wxid_example
python3 scripts/manage_wechat_accounts.py sync --all
```

`sync --all` 写入：

- `out/accounts/<wxid>/chat-export/`
- `out/accounts/<wxid>/customer-memory/`
- `out/accounts/accounts_manifest.json`

## 快速开始

安装 Python 和 Node 依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
npm --prefix .wx-cli-tools ci
```

运行环境检查：

```bash
python3 scripts/bootstrap_local_wechat.py --doctor-only
```

执行引导式安装、密钥捕获、聊天导出和客户记忆构建：

```bash
python3 scripts/bootstrap_local_wechat.py --run --with-memory
```

它会：

1. 检查依赖、WeChat.app、本地账号目录和 Frida 权限。
2. 必要时创建 `~/.wx-debug/WeChat-debug.app`。
3. 启动调试版微信进行密钥捕获。
4. 提示用户登录并打开一两个聊天。
5. 尝试自动识别 `<wxid>/db_storage`。
6. 将聊天导出到 `out/chat-export/export`。
7. 使用 `--with-memory` 时在 `out/customer-memory` 构建客户记忆。

如果检测到多个账号，请明确传入目标目录：

```bash
python3 scripts/bootstrap_local_wechat.py \
  --run \
  --with-memory \
  --wechat-root "$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage"
```

## 手动流程

创建用于密钥捕获的临时调试版微信：

```bash
mkdir -p "$HOME/.wx-debug"
rm -rf "$HOME/.wx-debug/WeChat-debug.app"
ditto /Applications/WeChat.app "$HOME/.wx-debug/WeChat-debug.app"
xattr -cr "$HOME/.wx-debug/WeChat-debug.app"
codesign --force --deep --sign - "$HOME/.wx-debug/WeChat-debug.app"
```

捕获本地数据库密钥材料：

```bash
python3 scripts/grab_wechat_key.py \
  --app "$HOME/.wx-debug/WeChat-debug.app/Contents/MacOS/WeChat" \
  --wait 240
```

导出聊天历史并构建客户记忆：

```bash
python3 scripts/export_chat_history.py \
  --output "$PWD/out/chat-export"

python3 scripts/build_customer_memory.py \
  --export-root out/chat-export/export \
  --out-root out/customer-memory
```

生成不包含原始消息正文的联系人活跃度报告：

```bash
python3 scripts/build_contact_activity_report.py \
  --export-root out/chat-export/export \
  --out-root out/contact-activity
```

为常用或高价值联系人生成私有 Wiki：

```bash
python3 scripts/build_contact_wiki.py \
  --activity-report out/contact-activity/contact_activity_report.json \
  --memory-root out/customer-memory \
  --out-root out/contact-wiki \
  --max-pages 200 \
  --clean
```

只生成回复草稿，不发送：

```bash
python3 scripts/auto_reply_once.py \
  --source wx-cli \
  --conversation "<联系人显示名称>" \
  --reply-source api \
  --dry-run \
  --duration 180 \
  --interval 3 \
  --context-messages 8 \
  --memory-root out/customer-memory \
  --memory-mode draft-only \
  --memory-use-policy auto \
  --service-knowledge-mode shadow \
  --emit-context-json \
  --max-replies 1
```

默认情况下，实时监听与回复生成只处理未静音的私聊。群聊、公众号、静音会话和通知状态未知的会话，会在进入模型草稿或 UI 发送路径之前被跳过。

把审核后的草稿粘贴到微信输入框，但不发送：

```bash
python3 scripts/wechat_ui_send.py \
  --search "<联系人显示名称>" \
  --text "<已审核草稿>" \
  --draft-only
```

`--draft-only` 会在按下 Enter 之前停止。只有真实发送得到明确批准时，才可以移除它。

## 实时回复服务

实时回复服务管理两个本地进程：

- `scripts/monitor_reply_candidates.py`：读取新消息并输出候选回复任务。
- `scripts/draft_reply_worker.py`：跳过疑似群发或推广消息，生成模型草稿，并仅粘贴到微信输入框。

预检、启动、查看与停止：

```bash
python3 scripts/wechat_reply_service.py doctor
python3 scripts/wechat_reply_service.py start --check-wx-cli
python3 scripts/wechat_reply_service.py status
python3 scripts/wechat_reply_service.py stop
```

服务需要用户授予本地微信访问与草稿粘贴权限。真实发送仍然不在自动流程内。完整说明见 [实时监听与仅草稿 Worker 手册](docs/live-monitor-and-draft-worker.md)。

## 客户记忆门控

客户记忆适合客服上下文，但不应注入每一个模型请求。默认 `auto` 策略：

- 订单、退款、退货、售后、物流、地址、历史承诺和支持动作类消息使用记忆。
- 职业建议、学习、趋势或抽象能力等宽泛问题跳过记忆。
- 调试时可用 `--memory-use-policy always` 或 `never` 覆盖。

## 服务知识话术

客户记忆与服务知识是两层不同的信息：

- 客户记忆描述某个具体客户可能发生过什么。
- 服务知识描述助手在常见客服场景中应该如何处理。

公开话术位于 `.project-wiki/wiki/reply-playbooks/`，覆盖订单状态、售后、物流、退款换货、地址修改、转人工和一般问题。

默认只观察话术匹配，不向模型注入：

```bash
python3 scripts/auto_reply_once.py \
  --source wx-cli \
  --conversation "<联系人显示名称>" \
  --reply-source api \
  --dry-run \
  --memory-root out/customer-memory \
  --memory-mode draft-only \
  --service-knowledge-mode shadow
```

要让草稿生成使用匹配到的话术，将最后一项改为：

```text
--service-knowledge-mode draft-only
```

## 微信收藏报告

最初的收藏可视化流程仍然可用：

```bash
python3 scripts/parse_favorites.py \
  --input "<decrypted favorite.db>" \
  --output out/favorites/data.json

python3 scripts/generate_report.py \
  --input out/favorites/data.json \
  --output out/favorites/report.html
```

生成的 HTML 报告包含统计、趋势、类型分布、来源排名、热力图、词云、标签、搜索、筛选和详情查看。

## 架构说明

自动回复原型采用以下分层：

```text
接收/读取：本地微信数据库或 wx-cli
决策：确定性规则 + 可选模型 API
记忆：结构化 JSON 档案 + 人工可读 Wiki
发送：本地微信 UI 自动化
验证：回读本地历史
```

它不是云端微信机器人。个人微信没有适合此场景的稳定公开 API，因此实际方案是在已登录的 Mac 上运行本地辅助进程。

更多文档：

- [聊天导出手册](docs/chat-export-runbook.md)
- [本地自动回复架构](docs/local-auto-reply-architecture.md)
- [安全与隐私](docs/security-and-privacy.md)
- [参考资料](docs/references.md)
- [项目 Wiki](.project-wiki/index.md)

`.project-wiki/` 只保存可公开的架构、操作、安全边界和服务话术；私有客户记忆始终留在 `out/customer-memory/`。

## 已知限制

- macOS 或微信版本变化可能破坏密钥捕获、数据库解析或 UI 自动化。
- 旧导出中的消息方向元数据可能不可靠，方向敏感的判断应优先使用最近实时上下文。
- 确定性提取出的客户事实可能有噪声，只能作为候选信息。
- 微信界面布局或焦点变化可能导致 UI 自动化异常。
- 本项目不是合规封装，也不会绕过平台规则与责任。

## 许可证

本项目使用 [MIT License](LICENSE)。
