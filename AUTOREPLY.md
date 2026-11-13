# 可选：微信自动回复

根据对应联系人的历史库模仿本人口吻。先验证草稿，再开启发送；默认不会发送。

首次使用请先按[从零开始教程](GETTING_STARTED.md)完成导出、清洗、切片和建库，得到该联系人的 `persona-rag.sqlite` 及同目录风格文件，再继续本页。

目前仅支持 Windows 私聊。读消息使用数据库，发消息使用 UIA 控件写入和一次模拟回车。发送时须打开目标会话、窗口不能最小化、桌面不能锁屏，且会占用前台焦点；没有自动切换联系人或静默后台发送功能。请先使用单个测试联系人。

## 1. 安装回复组件

**用途：** 在 README 的基础环境中加入 UIA 和无障碍激活辅助依赖。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-autoreply.txt
```

辅助依赖单独下载，不随仓库分发。固定提交及归属见 [PROVENANCE.md](PROVENANCE.md)。激活命令会核对源码哈希，不应随意升级依赖绕过检查。

## 2. 填写自己的配置

**用途：** 绑定聊天模型、向量模型和一个目标联系人，无需改 Python 代码。

```powershell
Copy-Item .\config\autoreply.example.json .\autoreply.json
.\.venv\Scripts\wechat-cli.exe contacts --query "对方的唯一备注名"
```

编辑 `autoreply.json`：

| 字段 | 填什么 |
| --- | --- |
| `wechat_config` | `wechat-cli init` 输出的读取配置路径 |
| `self_username` | 本人的 username |
| `chat.endpoint/model/token` | 完整 HTTPS chat/completions URL、聊天模型名称、key |
| `embedding` 下三项 | 与建库时 `credentials.json` 完全相同的向量服务配置 |
| `contacts[0].username` | 上面查询到的对方 username |
| `contacts[0].display_name` | 对方当前唯一备注名，没有备注时用昵称 |
| `contacts[0].index` | `work/episodes/persona-rag.sqlite` 或该联系人知识库路径 |
| `contacts[0].enabled` | 改为 `true`，启用这位联系人 |
| 全局 `enabled`、联系人 `auto_send` | 草稿阶段都保留 `false` |
| `allow_casual_self_state` | 允许“吃了、刚睡醒、在忙”等普通日常接话时设 `true`；默认 `false` |
| `persona_prompt_file` | 可留空；或填自己的口吻文本路径 |

JSON 路径建议用 `/`，例如 `C:/Users/你的用户名/.wechat-cli/config.json`；使用反斜杠时需写成 `\\`。相对路径从 `autoreply.json` 所在目录解析，不是从 `tools/` 解析。接口支持 OpenAI 兼容 Chat Completions、JSON 对象输出和 Bearer key；不是填写官网地址。

需要自定义口吻时：

```powershell
Copy-Item .\config\persona-prompt.example.txt .\persona-prompt.txt
```

编辑后将 `persona_prompt_file` 填为 `persona-prompt.txt`。描述本人如何接话即可，不要把历史联系人写死为当前对象。自己的配置、口吻文件和知识库均不要提交。

## 3. 先运行草稿

**用途：** 验证读取、检索和生成，不操作聊天输入框、不发送。

```powershell
.\.venv\Scripts\python.exe .\tools\wechat_autoreply.py run --config .\autoreply.json
```

启动后让测试联系人发一条**新的文字消息**。另开一个同目录终端查看：

```powershell
.\.venv\Scripts\python.exe .\tools\wechat_autoreply.py status
```

| 状态 | 含义 |
| --- | --- |
| `pending` | 等待合并消息或正在生成 |
| `draft` | 已生成草稿，未发送 |
| `review` | 内容需要本人核对，未发送；后续新消息仍会处理 |
| `ready` | 候选可以发送，正在等待发送条件 |
| `submitted` / `confirmed` | 已触发发送 / 数据库已出现同文本人消息，不代表对方已读 |
| `uncertain` | 发送结果不确定，不会自动重发 |
| `failed` / `superseded` | 生成失败 / 有新消息、人工接管或任务过期 |

默认每秒读取，等待2秒合并连发；只处理启动后的新消息。手动回复后该联系人暂停5分钟。媒体只保留占位，不让模型猜。

## 4. 开启控件并允许发送

**用途：** 在草稿效果确认后，让指定联系人实际收到回复。先在运行服务的终端按 `Ctrl+C` 退出，再操作。

手动打开微信里的目标会话并清空输入框中的本人草稿，然后执行：

```powershell
.\.venv\Scripts\python.exe .\tools\wechat_autoreply.py activate-uia
.\.venv\Scripts\python.exe .\tools\wechat_autoreply.py diagnose-uia
```

第一条会修改当前微信进程的 Qt 无障碍标志，使聊天控件可见；不修改磁盘程序，也不更改系统读屏设置。服务不会自行激活。第二条只读检查，详情保存在私有 `work/autoreply/uia-diagnostic.json`。

编辑 `autoreply.json`：将全局 `enabled`、该联系人 `enabled` 和 `auto_send` 都设为 `true`。随后运行：

```powershell
.\.venv\Scripts\python.exe .\tools\wechat_autoreply.py run --config .\autoreply.json --allow-send
```

配置开关和 `--allow-send` 必须同时满足才发送。先让测试联系人发一条新消息，再核对实际收到的内容。

适配器会核对会话标题、输入框名称、空草稿、焦点和任务版本，不匹配时停止。回复的多个气泡目前合为一条带换行的微信消息。写入后失焦可能留下草稿，不会自动删掉或重发；发送期间避免同时输入或切换焦点。

## 5. 暂停、退出与恢复

**用途：** 停止后续生成和发送，并还原本工具开启的标志。

```powershell
.\.venv\Scripts\python.exe .\tools\wechat_autoreply.py pause
```

产生 `work/autoreply/PAUSE` 文件，服务停止后续生成与发送。回到运行服务的终端按 `Ctrl+C` 退出。然后还原：

```powershell
.\.venv\Scripts\python.exe .\tools\wechat_autoreply.py restore-uia
```

还原只作用于本工具在同一微信进程中修改的标志。若要恢复测试，先删除暂停文件，再按需要启动草稿或发送服务：

```powershell
Remove-Item -LiteralPath .\work\autoreply\PAUSE
```

不存在该文件时无需删除。重启不补发旧任务。如果自定义 `--work`，运行、状态、暂停、激活和还原命令都要使用相同目录。

## 工作方式与排错

当前轮次取对方最后一组连续发言；遇到本人回复或120秒间隔便结束，旧消息只作上下文。最多检索3个历史片段，以真实 user/assistant 顺序展开示范，并保留本人连发气泡；历史与当前聊天明确分开。

开启日常状态代聊后，吃饭、睡觉、休息、忙不忙不因缺少当前状态确认而拦截；具体地点和重要承诺不在此授权内。默认严格模式会对常见状态断言尝试一次改写，仍不合格就转 `review`；重要事项、验证码及未解析媒体仍留待本人处理。这些规则不能保证覆盖全部表达或保证口吻准确。

- **收到新消息却没发：** 先看 `status`。`review` 是内容需核对，`ready` 通常是窗口、会话、草稿或焦点不满足；手动回复也会暂停5分钟。
- **一直 pending / failed：** 检查模型服务、额度、完整 URL，以及查询向量是否和知识库一致。
- **Ctrl+C 后仍有短暂请求：** 已发出的模型请求可能结束，但不会继续发送。
- **微信重启或版本变化：** 重新检查控件；不保证其他版本可用。

查看阶段耗时：

```powershell
.\.venv\Scripts\python.exe .\tools\wechat_autoreply.py timings
```

运行状态、草稿、控件诊断均是私人文件，只保存在本机。时长日志本身不保存消息或联系人。测试和验证范围见 [CONTRIBUTING.md](CONTRIBUTING.md) 与 `VALIDATION.json`；尚未验收长期托管、多账号和静默后台发送。
