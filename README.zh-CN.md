# pks — 个人知识流水线

把笔记、转写、字幕变成**可审核、可溯源**的知识条目。每条都能指回原文的某一行或某一毫秒。

不是笔记软件，也不是 AI 摘要器。它是导出文件堆和"你愿意信任的检索库"之间那一层。

```
原始文件  ──▶  解析文档  ──▶  知识条目  ──▶  关系边
（不可变）    （只有结构）    （主张+来源）    （候选，不合并）
```

English README: [README.md](README.md)

---

## 为什么做这个

大多数「第二大脑」都死于同一种病：抽出来的东西不可信，于是你不再读它。

具体两种死法：

**主张丢了地址。** 摘要说「复式记账之所以活下来是因为它把错误检测局部化了」。半年后你查不到这话是讲师说的、书里写的，还是模型自己编的。一旦查不了，整个库就废了。

**抽取会膨胀。** 把日记丢给模型说"提取知识"，它会给你三条自信的「理论」，内容是作者今天心情不好。重复一万次，库里九成是噪声。

这个流水线就是围绕「拒绝这两件事」建的：结构归结构，意义归意义；每个主张带一条能解析的指针；不确定性是一等字段，不是脚注。

---

## 三层

| 层 | 存什么 | 可变 | 能从哪重建 |
|---|---|---|---|
| **source** | 导出时的原始文件 + 清单行 | 永不 | — |
| **parsed** | `content`（逐字全文）+ `structure.blocks`（带类型和定位的片段） | 可 | source |
| **knowledge** | 原子主张 + 溯源 + 归属 + 认识论状态 | 只追加 | parsed |

每层都有 JSON Schema，而且是**带条件约束**的，不只是字段类型。Schema 自己会拒绝的：

- `content_form: verbatim` 却没有 `verbatim_quotes` → 拒绝
- `traceability: sourced` 却 `source_refs` 为空 → 拒绝
- `traceability: untraceable` 同时 `review_status: approved` → 拒绝
- `review_status: approved` 却没有 `review` 记录 → 拒绝
- `transcript_turn` 块缺 `speaker` 或 `start_ms` → 拒绝

意思是这些不变量在你没写的代码里也活着。

---

## 几个可以吵的设计决定

| 决定 | 被否掉的方案 | 理由 |
|---|---|---|
| `source_id` 一次性分配，永不重算 | 内容哈希、文件路径 | 改一个错字就换 key，所有引用全断；搬个文件夹同理。 |
| `document_id` 由 `source_id` 派生 | 随机 UUID | 重解析必须覆盖同一份文档，确定性 ID 让这事免费。 |
| ID 后缀用 Crockford base32 | hex、base64 | 没有 `I/L/O/U`。手抄、口述、OCR 都不会错认。 |
| 保留 `content` 逐字全文，不只存 blocks | 只存结构 | blocks 是视图，视图会有 bug；原始不能有。 |
| `source_type` 用 pattern 而非枚举 | 封闭枚举 | 加来源不该触发 schema 迁移。这是踩过坑改的，见 [lessons](docs/lessons.md)。 |
| 认识论状态独立于内容类型 | 合并成一个 type 字段 | "事实"是对世界的断言，"观点"是对作者的断言。合并就是模型把猜测洗成真理的方式。 |
| 关系是带标签的候选 | 自动合并近重复 | 相隔半年的两个矛盾信念是**证据**，不是噪声。合并掉就丢了时间线。 |
| 空输出是合法答案 | 每篇凑 N 条 | 有配额就一定有填充。 |

---

## 快速开始

```bash
pip install -e .

# 1. 解析：纯离线，不需要模型
pks parse examples/corpus -o out/parsed

# 2. 提取：需要 OpenAI 兼容端点
export PKS_API_KEY=sk-...
export PKS_API_BASE=https://api.openai.com/v1   # 可选
export PKS_MODEL=gpt-4o-mini                     # 可选
pks extract out/parsed -o out/items

# 3. 关系：默认规则版，加 --llm 用模型
pks relate out/items -o out/relations

# 4. 校验
pks validate out/items --documents out/parsed --relations out/relations

# 5. 看结果分布
pks inspect out/items
```

`examples/corpus/` 里是四个合成文件：技术笔记、双人讲座字幕、WebVTT 复盘会、一篇**基本不该**变成知识的日记。

---

## 一条真实条目长什么样

```jsonc
{
  "knowledge_id": "kn_01M6GGF48JFR80MY2R4GWK0Q89",
  "title": "Double-entry bookkeeping survived because it makes error detection local",
  "content": "Double-entry bookkeeping survived for six centuries ...",
  "content_form": "paraphrase",
  "domain": ["D03"],
  "content_type": "theory",
  "source_refs": [{
    "document_id": "doc_local_20261103_MWJEQF_01",
    "locator": { "kind": "timestamp", "timestamp_ms": 12480 },
    "quote": "The short answer is that it makes error detection local. ...",
    "quote_hash": "sha256:ea6b29b8..."
  }],
  "attribution": [{ "kind": "other_speaker", "name": null }],
  "epistemic_status": "unverified_claim",
  "confidence": 0.9,
  "review_status": "needs_review"
}
```

读作：**讲师在 00:00:12.480 说的这话；我们不把它当既成事实；还没人确认过。**

`quote_hash` 防的是静默漂移。转写重新导出、措辞变了，哈希就对不上，`pks validate` 会报 `quote_mismatch`，而不是继续端上旧主张。

---

## 命令

| 命令 | 联网 | 做什么 |
|---|---|---|
| `pks parse <dir>` | 否 | Markdown / SRT / VTT / TXT → 解析文档 |
| `pks extract <parsed>` | 是 | blocks → 知识条目，每块一次模型调用 |
| `pks relate <items>` | 可选 | 阻塞 + 打标签 → 候选边，不合并 |
| `pks validate <items>` | 否 | Schema、溯源、认识论守门、边一致性 |
| `pks inspect <items>` | 否 | 按领域/类型/归属看分布 |

`extract` 一次只发一块，且切分永远落在块边界上——所以引用不可能指进一个从没发出去的片段。

---

## 目录

```
schemas/     三个 JSON Schema，其它一切服从这个契约
rules/       领域分类、内容类型、治理规则（散文）
src/pks/
  ids.py       稳定标识与引用哈希
  parse.py     md / srt / vtt / txt → 带类型的定位块
  extract.py   分块、提示词、规范化、认识论守门
  relations.py 阻塞、打标、in/out 索引
  validate.py  那些否则一定会被跳过的检查
  cli.py       入口
examples/    合成语料 + 输出样例
docs/        架构说明，以及第一版错在哪的复盘
```

---

## 明确不做

- **不做存储引擎。** JSON 文件落盘。有理由了再加数据库。
- **不做向量检索。** 关系靠领域+标签阻塞，再让模型打标。向量召回是预留接口，不是依赖——真实原因写在 lessons 里。
- **不自动批准。** 流水线只能标 `needs_review`，只有人能标 `approved`。Schema 强制。
- **不合并。** 重复变成边。删除不是特性。
- **不接 Notion、不同步、不做 UI。** 那是下游问题。

---

## 已知局限

1. **领域判定是最弱的一环。** 给模型十一个代码加短标签，它照样会漂。要审。把**名称**而不是光秃秃的代码发给它，效果有可测提升（见 lessons），但没根治。
2. **关系召回受阻塞限制。** 跨领域的连接在构造上就看不见。这是有意的取舍，不是 bug。
3. **分块按字符预算。** 超长不分段的单块（三千字一整段）会掉质量。
4. **没有任何东西在评估抽取质量。** 要评估得有标注样本，要有标注样本得有人。

## License

MIT
