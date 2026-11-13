# 开发与测试

先按[入门教程](GETTING_STARTED.md)安装基础依赖；完整测试还需 [AUTOREPLY.md](AUTOREPLY.md) 的可选依赖。

## 代码入口

| 文件 | 作用 |
| --- | --- |
| `tools/export_for_cleaning.py` | 适配外部 wechat-cli，导出选中私聊和最小来源副本 |
| `tools/clean_wechat_persona.py` | 身份校验、日期筛选、去重和脱敏 |
| `tools/build_persona_episodes.py` | 多轮切片、气泡边界及双索引 SQLite 建库 |
| `tools/vectorize_persona_rag.py` | 向量请求、响应校验、归一化和续跑共用逻辑 |
| `tools/autoreply_core.py` | 检索、示范构造、内容策略和版本化队列 |
| `tools/autoreply_runtime.py` | 有界并发生成，按版本丢弃过期结果 |
| `tools/native_uia_sender.py` | 会话核验、控件写入与一次回车 |
| `tools/uia_activation.py` / `replica_integrity.py` | 显式激活、还原与第三方源码核对 |
| `tools/autoreply_metrics.py` | 不含正文或凭据的耗时日志 |
| `tools/wechat_autoreply.py` | 命令行入口与数据库轮询 |

## 运行检查

**用途：** 用虚构数据验证角色、脱敏、日期、切片、建库续跑、队列和 UIA 防误发条件，不读取真实账号，也不访问真实模型或发送消息。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
.\.venv\Scripts\python.exe .\tests\test_portable_pipeline.py
```

第二条是单独的合成集成测试，不包含在第一条的测试数量内。需要已经安装 README 中的外部 wechat-cli。

## 修改约定

- 样例与测试只用虚构数据；不提交聊天、账号、key、密钥、数据库、运行结果或日志。
- 改切片时检查双方角色、目标回复、上下文及数据集分区；相似度不等于口吻准确率。
- 改模型、接口或语料时新建索引，不混用不同来源的旧向量。
- 生成线程只读索引；队列更新和发送留在主线程，旧结果不可覆盖新消息。
- 发送保持一次尝试；不覆盖本人草稿，不自动重发不确定结果，不把按钮返回成功当作发送回执。
- 不引入隐藏进程激活或默认第三方发送驱动；修改激活逻辑须保留进程身份校验、哈希验证和还原能力。
- UIA 真机测试先只读检查；真实发送须明确接收对象和内容，不能由模拟测试代替验证。

本项目新增代码使用 Apache-2.0；第三方依赖保留原许可和归属。PR 请写明行为变化和实际执行的测试。清单 `MANIFEST.json` 记录交付文件的 SHA-256；更新发布包时重新生成，`VALIDATION.json` 只记录可核实的验证结果。
