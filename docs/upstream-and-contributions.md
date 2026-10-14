# 开源来源与贡献说明

整理日期：2026-10-15。依据 [验证摘要](public-snapshot-validation.json)，本文件只说明来源与改造，不替个人补造开发履历。

## 上游基础

- 仓库：[gavindsouza/inventory-management-system](https://github.com/gavindsouza/inventory-management-system)。
- 作者：Gavin D'souza；版权：Copyright (c) 2018 Gavin D'souza。
- 许可证：[MIT](../LICENSE)，保留完整原文。
- 基线提交：`d29d07109bc56b46c55e6d035e1cd5a53aaed123`。
- 原说明保存在 [README_UPSTREAM.md](../README_UPSTREAM.md)，保留该基线原说明，公开副本仅将已排除GIF嵌入改为上游链接；不是逐字节原版。

## 原功能与本次改造

| 范围 | 原项目已有 | 本次新增或调整 | 主要文件 |
|---|---|---|---|
| 商品 | 名称、总量、未分配量及编辑删除 | 新版编码、单位、阈值、历史停用；库存不能手改 | `inventory/app.py`、`templates/mvp/products.jinja` |
| 仓库与调拨 | 仓库管理、库存分配、仓库间调拨和历史 | 修复后保留为对照；不作为新版日常功能 | `inventory/legacy.py`、原模板 |
| 业务类型 | 没有明确的期初、采购和销售单据 | 三种业务，每单一个商品 | `inventory/app.py`、`templates/mvp/document*.jinja` |
| 单据状态 | 原调拨直接记录 | 草稿、确认、已过账只读、同单只确认一次 | `inventory/app.py` |
| 余额与记录 | 原来就有调拨历史及汇总 | 已过账业务流水求和、单据追溯、低库存提醒 | `inventory/app.py`、`templates/mvp/ledger.jinja`、`stock.jinja` |
| 基础缺陷 | 部分后端规则、连接和删除存在问题 | 关闭连接、正整数/库存检查、合法资料检查、友好报错、提交删除、表单防护、停用 | `inventory/legacy.py`、`inventory/app.py`、原表单模板 |
| 测试 | 原 6 个集成测试 | 保留原业务断言，新增 29 项基础回归与 38 项新业务检查 | `tests/` |
| 交付材料 | 原安装说明与演示素材 | 审查报告、演示脚本、个人验收表及发布清单 | `README.md`、`docs/` |

不要把原有“调拨历史”说成新增；新增的是有业务类型与来源单据的库存流水。仓库间调拨仍存在于原模块，但新版入口不开放，不能写成新版多仓能力。

## 人工智能与个人贡献边界

本次代码、测试和说明由人工智能辅助生成、修改或执行。用户提出并确认缩小后的范围及业务规则，参与操作学习和手工验证；具体个人验收以 [manual-acceptance.md](manual-acceptance.md) 的本人填写与截图为准，当前空表不是完成证明。

文件差异、上游历史与审查证据可以确认改了什么，无法证明每行都由个人独立编写，也不能确定所有个人工时。本次整理不提交代码、不调整提交日期、不改写原作者历史；后续第一次个人提交应使用真实时间并清楚说明人工智能辅助和开源基础。

适合表述：“基于 MIT 开源库存项目，参与单仓业务规则梳理，在人工智能辅助下实现并验收核心进销存流程。”不适合表述：“独立原创并上线企业系统”。

## 公开快照的来源查证

本仓库从不携带上游Git历史的干净快照独立建立。基线可从 [上游提交](https://github.com/gavindsouza/inventory-management-system/tree/d29d07109bc56b46c55e6d035e1cd5a53aaed123)查证，结合 [验证摘要](public-snapshot-validation.json) 与 [本副本验证摘要](public-snapshot-validation.json)。在原工作区才能使用Git比较基线，不在本仓库执行假定上游历史已导入的命令。

本轮公开快照仅调整素材引用、公开文档和检查配置；核心业务Python代码与原工作区内容一致。首次提交使用实际操作时间，不倒填时间或改写原作者历史。
