# 内容类型 (Content Types)

版本 `v1.0` · 对应 `knowledge_item.schema.json` 的 `content_type` 枚举

---

## 1. 与领域的区别

- **领域**：讨论什么 → 学科/主题坐标，可多选。
- **内容类型**：这条东西是什么性质 → 单一，互斥。

同一条录音里可以同时产出 `theory`（老师讲的理论）、`question`（提出的疑问）、`action_item`（布置的作业）。
拆开存，不要揉成一条 `meeting_record`。

---

## 2. 十四个类型

| 类型 | 判据（问自己） | 典型句式 | 常见误用 |
|---|---|---|---|
| `concept` | 这是在定义一个词/范畴吗？ | 「X 指的是……」 | 把完整理论拆成概念，丢失论证 |
| `theory` | 这是一套有内在逻辑的解释框架吗？ | 「因为 A 所以 B，机制是 C」 | 把个人猜测标成理论 |
| `fact` | 这是可被外部核验的陈述吗？ | 「2025 年 X 为 Y」 | 把转述当事实（应为 `unverified_claim`） |
| `opinion` | 这是某人的价值判断/偏好？ | 「我认为应该……」 | 与 `unverified_claim` 混用 |
| `question` | 这是一个待回答的开放问题？ | 「为什么……？」 | 把已解决的问题也存成 question |
| `method` | 这是可复用的做法/步骤？ | 「先做 A，再做 B」 | 写成结果而非步骤 |
| `research` | 这是一次有过程的探究及其发现？ | 「我查了 X、Y，结论是……」 | 与 `theory` 混用（research 重在过程与证据） |
| `decision` | 这是在多个选项中做了取舍？ | 「决定选 A，放弃 B，理由是……」 | 只记结论不记备选与理由 |
| `plan` | 这是未来要执行的安排？ | 「分三期，Q4 完成 X」 | 与 `action_item` 混用（plan 是集合） |
| `reflection` | 这是对自身经历/判断的回看？ | 「当时我以为……现在看……」 | 写成事件记录（应为 fact/meeting_record） |
| `source` | 这是一条资料线索本身？ | 「《国富论》第二篇第一章」 | 把资料内容与线索混为一条 |
| `action_item` | 这是有明确责任人的单个待办？ | 「李四 周五前交样本」 | 无责任人、无期限却标 action_item |
| `meeting_record` | 这是一次会议的整体记录（议程/结论）？ | 「参会人、议题、结论」 | 与 `transcript` 混用 |
| `transcript` | 这是逐字/逐句的原始转写？ | `Speaker 1 00:00:12 ……` | 加工后仍标 transcript |

---

## 3. 三组容易混的边界

### opinion vs unverified_claim

| | 用什么表达 |
|---|---|
| `content_type = opinion` | 这是**谁**的看法 |
| `epistemic_status = unverified_claim` | 这个说法**还没被验证** |

可以叠加：一条 `opinion` 通常 `epistemic_status = opinion`；
一条 `theory` 如果源自课堂讲授、未核实，则 `epistemic_status = unverified_claim`。

**绝不允许**：把「老师说的」写成 `fact` + `verified_fact`。

### meeting_record vs transcript

- `transcript`：原始逐句文本，一字不改，只加结构。
- `meeting_record`：会议的**结构化摘要**（议题、结论、待办），由人整理或 AI 生成后审核。
- 同一次会议二者并存，`meeting_record.source_refs` 指向 `transcript` 的块。

### decision vs plan vs action_item

```
decision  ── 选定了什么（含备选与理由）
plan      ── 打算怎么走（多步骤、跨时间）
action_item ── 谁、在什么时候、做什么（单步、可勾选）
```

一个 `decision` 通常派生多个 `action_item`，用 `related_knowledge_ids` 的 `part_of` 关联。

---

## 4. 分类时不做什么

1. **不追求一条知识覆盖全部内容**：一段 2 小时课程产出 10–30 条是正常的。
2. **不给每条都标 `meeting_record`**：那只是来源形态，不是知识性质。
3. **不把 AI 归纳直接标成 `fact`**：先给 `unverified_claim`，核实后再升级。
4. **不合并不同类型的相邻内容**：「理论 + 老师布置的作业」是两条。

---

## 5. 扩展规则

新增 `content_type` 需满足：

1. 现有 14 类无法表达，且强行归类会丢失关键信息（如责任、时效、证据强度）；
2. 能给出与其他类型的**一句话区分**；
3. 预计出现 ≥ 10 次。

新增会改 `knowledge_item.schema.json` 枚举 → bump `schema_version`（minor），并同步本文件版本号。

---

## 6. 受控枚举总览（供脚本引用）

```text
concept, theory, fact, opinion, question, method, research,
decision, plan, reflection, source, action_item,
meeting_record, transcript
```

`epistemic_status`：`verified_fact, unverified_claim, opinion, hypothesis, question, decision, plan, prediction, unknown`

`content_form`：`verbatim, paraphrase, summary, extraction`
