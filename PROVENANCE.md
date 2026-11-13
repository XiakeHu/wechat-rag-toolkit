# 第三方来源与本地集成

两个项目所需代码已经随本项目提供，初始化时无需再拉取其 GitHub 仓库。普通 Python 依赖仍需安装；根目录 `setup.ps1` 完成环境创建、本地 CLI 注册及依赖安装。

| 目录 | 来源 / 固定提交 | 保留范围 |
| --- | --- | --- |
| `vendor/wechat-cli` | TheRaptors/wechat-cli / `6770e1e0c0847e193fb3e3b22e4cd6b93de9aacc` | Windows 所需 Python 包、扫描器源码、构建元数据及 LICENSE；不带 macOS 二进制 |
| `vendor/wechatauto-replica` | fanyuantaier/wechatauto-replica / `bc2e6f2461330eb1a53996d2f2afc18416376b52` | 裁剪后的 accessibility.py 和 LICENSE；本地最小初始化及日志适配由本项目新增 |

上游原始哈希与本地修改均有记录；已裁剪非 Windows 扫描器、多余 CLI 命令和未调用的激活接口，移除密钥与 salt 的终端输出及异常原文回显，保留校验和完整本机保存。现有 CLI 仅保留 init、contacts，逐文件原始 SHA-256 见 `vendor/SOURCES.json`。无障碍辅助仍按 `config/replica-audit.json` 核对。没有引入上游默认 GUI、OCR、混合发送、媒体或数据库驱动；本地日志适配避免导入时修改根日志、标准输出或创建日志文件。

`tools/bootstrap_vendor.py` 优先绑定项目内代码；`tools/wechat_reader.py` 提供直接运行入口。也可以执行 `setup.ps1` 注册 `.venv/Scripts/wechat-cli.exe`。首次需安装 Python 3.11；默认安装全部本项目所需依赖，`-DataOnly` 只安装数据处理部分。

两个直接依赖的 LICENSE 标注 Apache-2.0，已随对应目录保留。wechat-cli 声明基于 freestylefly/wechat-cli，并引用 ylytdeng/wechat-decrypt、TANGandXUE/wcdb-key-tool 等工作；继续保留这些归属。直接许可声明不等于完整转引链已审计，本项目不替上游重新授权。

本项目新增代码采用根目录 Apache-2.0，第三方权利不因此改变。具体归属见 NOTICE。本项目与微信官方无隶属关系。

README 按维护者要求保持原样；其中“单独下载第三方源码”的说明属于旧安装方式，当前交付已经包含上述代码，可直接使用 setup.ps1。
