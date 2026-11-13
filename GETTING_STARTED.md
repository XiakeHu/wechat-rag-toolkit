# 从零开始：导出、清洗和建立知识库

本教程面向第一次使用项目的读者。项目用途与整体流程见 [README](README.md)，自动回复见 [AUTOREPLY.md](AUTOREPLY.md)。

完成后你会得到：清洗后的消息、多轮 RAG 文档和一个包含正文及向量的 SQLite 知识库。无需旧向量库或旧备份，但所需历史必须已经在电脑微信中；本教程不会恢复已丢失的记录。

**执行位置：** 下载并解压项目后，在 README 所在文件夹打开 PowerShell。以下命令逐条执行，上一条报错时先停下；不要同时运行两种清洗示例。输出默认存入 `work/`，保留原始记录。

## 1. 安装环境

**用途：** 创建独立环境，安装数据处理组件及固定版本的第三方微信读取工具。需要联网，只做一次。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps "https://github.com/TheRaptors/wechat-cli/archive/6770e1e0c0847e193fb3e3b22e4cd6b93de9aacc.zip"
```

**结果：** 生成 `.venv/`，安装无报错。后续直接使用这个环境，不用执行激活命令。

## 2. 初始化并确认双方账号

**用途：** 从本机已登录微信获取数据库读取配置，确认谁是本人、谁是对方。这不是用账号密码登录，也不会发送消息。

```powershell
.\.venv\Scripts\wechat-cli.exe init
.\.venv\Scripts\wechat-cli.exe contacts --query "你的微信昵称"
```

记下属于本人的 `username`，不是昵称或手机号。给目标联系人设置唯一备注，再运行：

```powershell
$本人账号 = Read-Host "本人的 username"
$对方备注 = Read-Host "对方的唯一备注名"
```

**结果：** 读取配置通常位于 `$env:USERPROFILE\.wechat-cli\config.json`，密钥也保存在该目录。实际位置以初始化输出为准；不同位置请修改下方 `--config`。若权限不足，按工具提示在有相应权限的终端重试。关闭终端后需重新填写上面两个变量。

## 3. 导出指定私聊

**用途：** 保存选中会话的消息及核对发送人的最小来源副本。此步只在本机读取，不上传聊天。

```powershell
.\.venv\Scripts\python.exe .\tools\export_for_cleaning.py --config "$env:USERPROFILE\.wechat-cli\config.json" --self-username $本人账号 --partner-name $对方备注 --output .\work\snapshot
```

**结果：** 生成 `work/snapshot/`。这里含原始私聊和来源数据库，务必留在本机。

## 4. 清洗和脱敏

**用途：** 校验双方身份、排序去重，替换手机号、邮箱、长号码、密钥、链接等常见敏感内容。此步不联网。

```powershell
.\.venv\Scripts\python.exe .\tools\clean_wechat_persona.py --source .\work\snapshot --output .\work\clean --self-username $本人账号 --partner-name $对方备注
```

**结果：** `work/clean/clean_messages.jsonl` 是清洗后的消息，`cleaning_report.json` 是处理报告。

需要限定日期或替换姓名、地址时，先复制并编辑自定义规则：

```powershell
Copy-Item .\config\redactions.example.json .\redactions.json
```

然后用以下命令**替代**上面的基础清洗命令，并修改示例日期：

```powershell
.\.venv\Scripts\python.exe .\tools\clean_wechat_persona.py --source .\work\snapshot --output .\work\clean --self-username $本人账号 --partner-name $对方备注 --start-date 2026-01-01 --end-date 2026-10-31 --redactions .\redactions.json --redaction-policy replace
```

日期按北京时间 UTC+8，含起止当天；可只填一端，不填则不限。`replace` 允许替换后的消息用于建库；默认 `exclude` 会排除涉及脱敏内容的回复回合。自动规则可能漏掉自由文本中的隐私，姓名、昵称和地址请自行补充并检查。

## 5. 切成多轮片段

**用途：** 保留双方聊天顺序、上下文和本人连发气泡，生成适合 RAG 的文档。此步不联网。

```powershell
.\.venv\Scripts\python.exe .\tools\build_persona_episodes.py prepare --source .\work\clean --output .\work\episodes
```

**结果：** `work/episodes/rag_documents.jsonl` 是切片文档，`report.json` 是统计。打开文档检查身份、上下文和隐私；切片边界由规则判断，不能保证语义完全准确。

## 6. 配置向量服务并建库

**用途：** 将处理后的发言和相关上文发送给你选择的向量服务，生成可做语义检索的索引。

```powershell
Copy-Item .\config\credentials.example.json .\credentials.json
```

编辑 `credentials.json` 的三项：

| 字段 | 填什么 |
| --- | --- |
| `embedding_endpoint` | 完整 HTTPS embeddings 接口 URL，不是官网或聊天接口 |
| `embedding_model` | 向量模型名称 |
| `embedding_token` | 该服务的 API key |

模板全部留空，不绑定厂商。接口须支持 OpenAI 兼容 embeddings、Bearer 认证和批量输入，维度自动识别。确认愿意向该服务发送清洗后的内容及接受其费用后运行：

```powershell
.\.venv\Scripts\python.exe .\tools\build_persona_episodes.py embed --source .\work\clean --output .\work\episodes --config .\credentials.json
```

**结果：** `work/episodes/persona-rag.sqlite` 同时保存片段正文、元数据和向量；请保留整个结果目录。建库不需要聊天模型。中断可重跑同一命令复用已完成向量；查询仍需同一向量服务和模型。

## 7. 可选：根据历史口吻回复

**用途：** 用聊天模型结合知识库回复新消息。先生成草稿，确认效果后再开启实际发送。

详见 [自动回复教程](AUTOREPLY.md)。填写自己的 `autoreply.json`，无需把 key 写入代码。当前发送需要手动打开目标会话、微信窗口可见且桌面解锁，会占用前台焦点；不支持静默后台发送、自动切换联系人或群聊。

## 常见问题

| 问题 | 处理方式 |
| --- | --- |
| 没找到会话或历史太少 | 核对账号、唯一备注和电脑上的实际历史；只登录不会同步全部旧记录 |
| 没有可靠片段 | 放宽日期，检查媒体占比、脱敏策略和双方身份；至少需要适合的双方文字对话 |
| 输出目录已存在 | 为这次运行换一个新目录；不要覆盖或删除原始记录 |
| 换日期、脱敏规则、模型 | 用新的清洗和切片目录重新建库，不能混用旧向量 |
| key、额度、网络报错 | 核对完整接口 URL、模型、权限及服务额度；不要把 key 发在 issue 中 |
| 微信版本不兼容 | 先核对读取工具支持情况；本项目不保证所有版本可用 |
