# 数据治理规则 (Data Governance)

版本 `v1.0` · 2026-10-20 · 适用于 `source_manifest` / `parsed_document` / `knowledge_item` 三层

---

## 1. 三层状态机（互不替代）

### 1.1 Source 层：文件处理状态

`source_manifest.processing_status`

```
pending ──▶ parsing ──▶ parsed
   │            │
   │            ├──▶ failed ──(修复后重跑)──▶ parsing
   └──▶ skipped_duplicate
```

| 状态 | 含义 | 能否重跑 |
|---|---|---|
| `pending` | 已入库，未解析 | 是 |
| `parsing` | 解析中（需带超时，防僵尸） | 是 |
| `parsed` | 已产出 Parsed Document | 可重解析，结果幂等覆盖 |
| `failed` | 解析失败，错误原因进 `05_Logs` | 是，必须修，不得跳过 |
| `skipped_duplicate` | 内容哈希重复，`duplicate_of` 指向首份 | 是（若首份失败可切换） |

> `parsing` 超过 30 分钟未更新 → 由巡检任务改回 `pending` 并记一条 `STALE_PARSING`。

### 1.2 Document 层：解析质量

`parsed_document.parsing_status`

| 状态 | 含义 | 后续动作 |
|---|---|---|
| `success` | 结构完整 | 进入分类 |
| `partial` | 主内容可读但有丢失（见 `parsing_warnings`） | 进入分类，同时标记待抽检 |
| `failed` | 无可用内容 | 落 `02_Processing/Failed/`，不进入分类 |

### 1.3 Knowledge 层：审核状态

`knowledge_item.review_status`

```
pending ──▶ processing ──▶ needs_review ──▶ approved
   │            │                │
   │            └──▶ failed      └──▶ rejected
   └──────────────────────────────▶ needs_review
```

| 状态 | 含义 | 可否出现在 `03_Knowledge_Export` |
|---|---|---|
| `pending` | 已建条目，未处理 | ❌ |
| `processing` | 提取/分类/关联进行中 | ❌ |
| `needs_review` | 需人工判断 | ❌ |
| `approved` | 人工确认 | ✅ 唯一允许 |
| `rejected` | 人工否决，保留记录与理由 | ❌ |
| `failed` | 处理异常 | ❌ |

**状态只能通过 `processing_history` 里的对应动作变更**，不允许"裸改"。

---

## 2. 十条质量规则的落地方式

| # | 规则 | 落地机制 |
|---|---|---|
| 1 | 所有条目可追溯 | `source_refs` 必填 + `traceability` 三态；不可追溯须显式标记且**禁止 approved**（schema 已强制） |
| 2 | 原文 / AI 提取 / 人工修订分离 | 原文只存 `01_Raw_Archive`（只读）+ `parsed_document.content`；AI 产出存 `knowledge_item`；修订走 `processing_history.actor_type=human`，不覆盖原值 |
| 3 | 不得把 AI 推断写成用户原话 | `content_form` 四态；`verbatim` 必须有 `verbatim_quotes`（schema 强制）；`attribution.kind` 区分 `user` 与 `ai_inference` |
| 4 | 讨论中的观点不得当事实 | `epistemic_status` 默认 `unverified_claim`/`opinion`；升 `verified_fact` 需 `processing_history` 有 `verify` 记录 |
| 5 | 文本相似不得自动合并 | `dedupe_fingerprint` 只生成 `related_knowledge_ids.status=candidate`；合并必须人工 `approve` |
| 6 | 去重保留来源关系 | 重复文件保留、`duplicate_of` 标记不删；重复知识用 `supersedes` + 保留旧条目 |
| 7 | 低置信/来源不明/归属不明/冲突 → 人工 | 见 §3 触发规则 |
| 8 | 自动化操作有日志且可定位 | `processing_history`（条目级）+ `05_Logs/*.md`（运行级），均带 `input_ref` |
| 9 | 失败不得静默跳过 | 每个失败必须落 `02_Processing/Failed/` 或 `processing_status=failed`，并写日志；无日志的跳过视为缺陷 |
| 10 | 未审核不得覆盖已审核 | 写入前检查目标 `review_status=approved` → 拒绝覆盖，改为新建条目并 `supersedes` 旧条目，旧条目转 `rejected` 或标记 `superseded` |

---

## 3. 自动进入 `needs_review` 的条件

命中任一即转人工，**不得自动 approved**：

| 触发 | 字段依据 | required_reasons |
|---|---|---|
| 置信度 < 0.70 | `confidence` | `low_confidence` |
| 溯源不完整 | `traceability = partial` | `ambiguous_source` |
| 完全无法溯源 | `traceability = untraceable` | `ambiguous_source` |
| 归属为 unknown | `attribution[].kind = unknown` | `ambiguous_attribution` |
| AI 自行归纳且无原文对应句 | `attribution.kind = ai_inference` 且 `verbatim_quotes` 为空 | `model_uncertain` |
| 同一主题不同来源结论冲突 | 关联中出现 `relation = contradicts` 且两方均非 `unknown` | `conflicting_sources` |
| 要把 `epistemic_status` 升为 `verified_fact` | 缺 `verify` 记录 | `unverified_claim` |
| 拟合并两条知识 | 任意 `related_knowledge_ids.status = candidate` 且 `relation in {related, refines}` | `model_uncertain` |
| 领域取 3 个且互不相邻 | `domain` 长度 = 3 | `model_uncertain` |

**人工抽检**：`parsed` 且 `parsing_status=partial` 的文档，每周随机抽 5% 核对，结果记 `manual_spot_check`。

---

## 4. 去重策略

分三层，粒度不同，全部**只标记不删除**：

| 层级 | 判据 | 处理 |
|---|---|---|
| 文件级 | `content_hash` 相同 | `duplicate_of` 指向首份，状态 `skipped_duplicate`，文件保留 |
| 文档级 | 同 `source_id` 重解析 | `document_id` 确定性相同，幂等覆盖，保留 `parser.version` 变更记录 |
| 知识级 | `dedupe_fingerprint` 相同 | 建 `related` + `candidate`，等人确认；确认后旧条目 `supersedes`，不物理删除 |

> 指纹算法：`sha256(归一化 content | 排序后的 domain | content_type)` 取前 64 hex。
> 归一化：去空白、统一全半角、去标点。**不做语义相似度自动合并。**

---

## 5. 不可变性

| 层 | 允许 | 禁止 |
|---|---|---|
| `01_Raw_Archive` | 追加新文件、改 `source_manifest.csv` 的状态列 | 改文件内容、重命名、删除、原地覆盖 |
| `02_Processing` | 全量重跑、覆盖中间产物 | 作为唯一副本（必须有源可回溯） |
| `03_Knowledge_Export` | 新增、随审核状态更新 | 存放未 approved 条目 |
| `05_Logs` | 追加 | 修改历史日志（纠错另起一条） |

任何"修正原文"的需求 → 新建一条 `source`，`notes` 注明 `corrects: src_xxx`，原文件不动。

---

## 6. 日志规范

**运行级**（`05_Logs/YYYY-MM-DD.md`）每次批处理追加一段：

```markdown
## <任务名> HH:MM
- 输入：<目录或查询条件>
- 处理：N 条，成功 X，跳过 Y，失败 Z
- 失败明细：<文件名> — <错误码> — <原因>
- 输出：<目标目录>
```

**条目级**（`processing_history[]`）每次机器或人工动作追加一条，必须含
`step / actor_type / actor_name / actor_version / at / input_ref / result`。

追溯链要求：任取一条 `knowledge_item`，能沿 `source_refs → document_id → source_id → 实际文件` 走通；断链即为缺陷。

---

## 7. 版本与迁移

- 三个 schema 各自 `schema_version`，同时变更时取最大版本并统一 bump。
- 枚举变更（领域、内容类型、状态）**必须**伴随迁移脚本与映射表，写在 `05_Logs/migrations/`。
- 迁移不删旧值：旧枚举值保留在历史条目中，查询层做映射视图。
- 每次批量重跑前，先备份 `02_Processing/` 与 `03_Knowledge_Export/`（原始层永不备份，因为它本身不可变）。

---

## 8. 人工审核的最小操作

一条 `needs_review` 条目，人工只需做三件事之一：

1. **approve** — 补 `reviewer` / `reviewed_at` / `decision`，必要时改 `epistemic_status`；
2. **reject** — 写 `note` 说明理由，条目保留不删；
3. **request_changes** — 回到 `pending`，由下一轮处理重跑。

> 审核不是重写。若内容本身要改，新建条目并 `supersedes`，保持原判断可回溯。
