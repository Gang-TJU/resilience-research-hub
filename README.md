# 资源安全与韧性科研情报站 V1.0
零新增费用目标：公开 GitHub Pages + 标准 GitHub Actions + Python 标准库。仅公开题名、来源、日期、链接及基础元数据；不镜像全文，不使用付费模型。规则分类及统计不等于 AI 导读。
每日北京时间 05:17、17:37 检查，初次覆盖近30天论文；按来源保留历史记录。数据目录条目不是原始数据下载。源站失败独立记录，旧记录保留。
运行：python3 collect.py；结果 data/archive.json、site/data.json。Pages Source 选择 GitHub Actions。工作流仅在公开仓库运行，支持手动触发；私有仓库不会启动运行器。首次切换公开后可从 Actions 手动运行一次，或等待定时任务。GitHub 调度可能延迟。
本项目不声称已验证全部信源；请以站点运行状态为准。