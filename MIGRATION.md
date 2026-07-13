# 新机迁移与开箱运行手册

这份文档面向“把 `wechat-local-service-kit` 压缩包或私有 GitHub 仓库交给另一台 Mac，然后尽量少解释就能跑”的场景。

先说结论：

- **只迁移代码**：可以跑脚本、看文档、重新接入新机上的微信数据。
- **迁移代码 + `out/`**：可以离线查看/查询已经导出的 customer memory、contact wiki、报告。
- **迁移代码 + `out/` + `.wx-cli-*` profile**：可以复用已有 key/profile，但必须是同一 owner 的可信 Mac，并且通常要改 `db_dir` 路径。
- **只拿项目包，没登录微信、没本机 `db_storage`、没 key**：不能直接读取新机微信历史。

## 1. 新机需要安装什么

目标环境：

- macOS
- WeChat for macOS 4.x，安装在 `/Applications/WeChat.app`
- Python 3.10+
- Node.js 18+ 和 npm
- Git，可选但推荐

Python 依赖在 `requirements.txt`：

- `frida`, `frida-tools`：抓取本机 WeChat PBKDF2/key 相关事件
- `pycryptodome`：SQLCipher/AES 解密
- `zstandard`：解析部分 zstd 压缩消息内容

Node 依赖在 `.wx-cli-tools/package.json`：

- `@jackwener/wx-cli`：本地微信数据 CLI，GitHub: <https://github.com/jackwener/wx-cli>

## 2. 第一次安装

进入项目目录：

```bash
cd /path/to/wechat-local-service-kit
```

创建 Python 虚拟环境并安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

安装 repo-local `wx-cli`：

```bash
npm --prefix .wx-cli-tools ci
```

如果压缩包里没有 `.wx-cli-tools/package-lock.json`，用这个兜底：

```bash
mkdir -p .wx-cli-tools
npm --prefix .wx-cli-tools install @jackwener/wx-cli@0.1.10
```

确认工具可用：

```bash
./.wx-cli-tools/node_modules/.bin/wx --version
python3 scripts/wechat_tool_status.py --skip-daemon --compact
```

## 3. 如果只是查看随包带来的导出结果

前提：压缩包里包含 `out/`。

可以先看有哪些账号/导出：

```bash
find out/accounts -maxdepth 2 -type d | sort
```

查询 customer memory：

```bash
python3 scripts/query_customer_memory.py \
  --memory-root out/customer-memory \
  --query "<联系人名或关键词>" \
  --limit 3
```

如果是账号隔离目录：

```bash
python3 scripts/query_customer_memory.py \
  --memory-root "out/accounts/<wxid>/customer-memory" \
  --query "<联系人名或关键词>" \
  --limit 3
```

渲染联系人页面：

```bash
python3 scripts/render_customer_pages.py \
  --memory-root out/customer-memory \
  --conversation "<联系人名>" \
  --limit 1
```

这类离线查看不需要新机登录微信，也不需要重新抓 key。

## 4. 如果要在新机读取本机微信数据

前提：

1. 新机已安装 WeChat。
2. 新机已登录目标微信账号。
3. 本地存在：

```text
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage
```

先让微信加载更多本地数据库：

- 打开 2 个以上私聊。
- 打开 1 个以上群聊。
- 往上翻几屏历史记录。
- 等聊天列表和消息内容稳定加载。

运行环境检查：

```bash
python3 scripts/bootstrap_local_wechat.py --doctor-only
```

如果要走完整 Frida 抓 key + 导出 + customer memory：

```bash
python3 scripts/bootstrap_local_wechat.py --run --with-memory
```

如果检测到多个账号，显式指定 `db_storage`：

```bash
python3 scripts/bootstrap_local_wechat.py \
  --run \
  --with-memory \
  --wechat-root "$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage"
```

更详细的新账号流程见：

- [docs/wechat-new-account-runbook.md](docs/wechat-new-account-runbook.md)
- [docs/chat-export-runbook.md](docs/chat-export-runbook.md)

## 5. 复用随包携带的 `.wx-cli-*` profile

只有在同一 owner 的可信 Mac 之间迁移时，才考虑携带这些私密目录：

```text
.wx-cli-profile
.wx-cli-<profile>/
  config.json
  all_keys.json
```

新机上通常要改 `.wx-cli-<profile>/config.json` 里的 `db_dir`，让它指向新机实际路径：

```json
{
  "decrypted_dir": "decrypted",
  "db_dir": "/Users/<new-user>/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage",
  "keys_file": "all_keys.json"
}
```

`.wx-cli-profile` 内容应指向当前 profile，例如：

```text
.wx-cli-<profile>
```

检查 profile，不打印 key：

```bash
python3 scripts/manage_wechat_accounts.py list
python3 scripts/wechat_tool_status.py --skip-daemon --compact
```

如果 `key_count = 0`，这个 profile 不算可用，需要重新抓 key。

## 6. 重新抓 wx-cli key

如果要让 `wx-cli` 在新机上重新建立 key/profile：

```bash
./.wx-cli-tools/node_modules/.bin/wx init --force
```

如果 macOS 需要管理员权限，可通过系统弹窗执行：

```bash
osascript -e 'do shell script "cd /path/to/wechat-local-service-kit && ./.wx-cli-tools/node_modules/.bin/wx init --force" with administrator privileges'
```

成功后，`~/.wx-cli/all_keys.json` 会包含当前提取的 key。把它复制到 repo-local profile：

```bash
mkdir -p ".wx-cli-<wxid>"
cp "$HOME/.wx-cli/all_keys.json" ".wx-cli-<wxid>/all_keys.json"
chmod 600 ".wx-cli-<wxid>/all_keys.json"
```

再创建/更新 `.wx-cli-<wxid>/config.json`，并让 `.wx-cli-profile` 指向它。

## 7. macOS 权限

Frida/key capture 常见需要：

- Developer Tools 权限
- Terminal/iTerm/Codex/实际运行命令的宿主 App 权限
- 必要时启用 DevToolsSecurity

检查：

```bash
python3 scripts/test_frida_attach.py
```

如果失败，按脚本输出的提示打开系统设置：

```bash
python3 scripts/test_frida_attach.py --open-settings
```

如果要自动把草稿粘贴到 WeChat 输入框，还需要：

- 系统设置里的 Accessibility 权限
- 给实际运行 `osascript` 的宿主 App 授权

只生成草稿、不粘贴、不发送，则不需要这一步。

## 8. 模型 API 配置

只有 reply draft / live worker 需要 OpenAI-compatible API。离线导出、customer memory 查询不需要。

```bash
export OPENAI_BASE_URL="https://<your-compatible-api-base>"
export OPENAI_API_KEY="<your-api-key>"
export OPENAI_MODEL="<model-name>"
```

不要把 API key 写入 README、commit、聊天记录或共享压缩包。

## 9. 推荐打包方式

### 代码包，可发给协作者

不包含私密数据：

```bash
cd /path/to
tar \
  --exclude='wechat-local-service-kit/.git' \
  --exclude='wechat-local-service-kit/.venv' \
  --exclude='wechat-local-service-kit/out' \
  --exclude='wechat-local-service-kit/decrypted' \
  --exclude='wechat-local-service-kit/.wx-cli-profile' \
  --exclude='wechat-local-service-kit/.wx-cli-tools/node_modules' \
  --exclude='wechat-local-service-kit/.wx-cli-big' \
  --exclude='wechat-local-service-kit/.wx-cli-wxid_*' \
  --exclude='wechat-local-service-kit/scripts/__pycache__' \
  -czf wechat-local-service-kit-code.tgz \
  wechat-local-service-kit
```

对方拿到后需要自己登录 WeChat、安装依赖、抓 key、导出数据。

如果你有自定义命名的 `.wx-cli-<profile>` 账号目录，也要在代码包里排除；`.wx-cli-tools/package.json` 和
`.wx-cli-tools/package-lock.json` 可以保留，它们只用于安装 `wx-cli`。

### 私有迁移包，只给自己的可信 Mac

包含 `out/` 和 repo-local profile，但不包含 node_modules、Python venv、cache：

```bash
cd /path/to
tar \
  --exclude='wechat-local-service-kit/.git' \
  --exclude='wechat-local-service-kit/.venv' \
  --exclude='wechat-local-service-kit/.wx-cli-tools/node_modules' \
  --exclude='wechat-local-service-kit/.wx-cli-*/cache' \
  --exclude='wechat-local-service-kit/decrypted' \
  --exclude='wechat-local-service-kit/scripts/__pycache__' \
  -czf wechat-local-service-kit-private-transfer.tgz \
  wechat-local-service-kit
```

这个包可能包含聊天导出、customer memory、`all_keys.json`、本地路径。只适合自己控制的机器之间迁移。

## 10. GitHub / Git clone 路线

如果这个项目已放在私有 GitHub 仓库，新机可以：

```bash
git clone <YOUR_PRIVATE_REPO_URL> wechat-local-service-kit
cd wechat-local-service-kit
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
npm --prefix .wx-cli-tools ci
```

GitHub clone 默认不会包含：

- `out/`
- `.wx-cli-profile`
- `.wx-cli-*`
- `decrypted/`
- `*.db`
- `all_keys.json`
- `config.json`

所以 clone 之后要么重新接入新机微信，要么再单独通过私有安全渠道迁移这些本地私密资产。

## 11. 最小验收清单

新机完成迁移后，至少跑：

```bash
python3 scripts/bootstrap_local_wechat.py --doctor-only
python3 scripts/wechat_tool_status.py --skip-daemon --compact
python3 scripts/manage_wechat_accounts.py list
```

如果已有导出：

```bash
python3 - <<'PY'
from pathlib import Path
import json

for manifest_path in sorted(Path("out").glob("**/manifest.json")):
    try:
        data = json.loads(manifest_path.read_text())
    except Exception:
        continue
    if "total_conversations" in data or "total_messages" in data:
        print(manifest_path, data.get("total_conversations"), data.get("total_messages"))
PY
```

如果要测试 live `wx-cli` 读取，只先做只读检查：

```bash
python3 - <<'PY'
import sys
sys.path.insert(0, "scripts")
from wx_cli_adapter import list_sessions

print(list_sessions(limit=1))
PY
```

如果这里出现 `file is not a database`，不要直接删除 cache。先确认 active profile、`db_dir`、key_count 和 wx-cli cache 映射，再备份后重建。
