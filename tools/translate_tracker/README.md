# 翻译追踪器 (translate-tracker)

基于 Git 历史的双语文档翻译状态分析工具。

给定一个源语言目录（如 `docs/en/`）和一个目标语言目录（如 `docs/zh/`），本工具通过对比 Markdown 文件的结构特征，并结合 `git blame` 获取的逐行提交历史，自动判定每个文件的翻译状态。

---

## 核心功能

- **文件级比对**：识别目标缺失（需全文翻译）和源缺失（目标过时需删除）的文件。
- **块级结构对齐**：将 Markdown 文件拆分为语义块（标题、段落、表格、代码块、admonition、列表等），通过**弱匹配签名**跨语言对齐。
- **Git 历史驱动**：利用 `git blame --porcelain` 获取每行最后修改的 commit 和时间戳，精确判断目标文件的某块翻译是否已落后于源文件的更新。
- **Tag 停止机制**：支持指定 tag 前缀（如 `translate-reviewed/`），扫描到该 tag 时停止追溯，用于标记已人工 review 的基准点。
- **多格式输出**：支持纯文本、Markdown、JSON 三种报告格式。

---

## 安装

无需额外依赖，仅需 Python 3.8+ 和 Git。

```bash
cd tools/translate_tracker
python -m translate_tracker --help
```

---

## 快速开始

### 默认运行

```bash
python -m tools.translate_tracker
```

默认比较 `docs/en/` 和 `docs/zh/`，输出纯文本报告到终端。

### 常用示例

```bash
# 指定源目录和目标目录
python -m tools.translate_tracker --source docs/en --target docs/zh

# 使用 Tag 停止机制（扫描到 translate-reviewed/ 前缀的最新 tag 为止）
python -m tools.translate_tracker --stop-tag-prefix "translate-reviewed/"

# 输出 Markdown 格式报告到文件
python -m tools.translate_tracker --format markdown --output report.md

# 输出 JSON 格式（供 CI 或自动化工具消费）
python -m tools.translate_tracker --format json --output report.json

# 指定 Git 仓库根目录（自动探测失败时使用）
python -m tools.translate_tracker --git-cwd /path/to/repo

# 调整整文件阈值（默认 0.5）
# 当非 unchanged 块的比例超过此值时，直接报告"整文件需重新翻译"
python -m tools.translate_tracker --threshold 0.3
```

---

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--source`, `-s` | `docs/en/` | 源语言目录 |
| `--target`, `-t` | `docs/zh/` | 目标语言目录 |
| `--stop-tag-prefix` | `None` | Tag 前缀，扫描停止于此 tag |
| `--format`, `-f` | `text` | 输出格式：`text` / `markdown` / `json` |
| `--output`, `-o` | `stdout` | 输出文件路径 |
| `--threshold` | `0.5` | 整文件摘要阈值 |
| `--git-cwd` | 自动探测 | Git 仓库根目录 |

---

## 翻译状态说明

工具将文件划分为以下状态：

| 状态 | 含义 |
|------|------|
| **整体缺失** | 目标文件不存在，源文件存在 → 需全文翻译 |
| **整体过时** | 源文件已删除，目标文件仍存在 → 需删除 |
| **结构缺失/过时** | 文件存在，但块结构不一致（新增/删除/内容过时），会标注具体的源文件行范围 |
| **翻译正常** | 源和目标结构一致，且目标翻译不落后于源更新 |
| **警告** | Git 历史无法追溯至 stop-tag 之前，状态无法确认 |

---

## 设计概要

### 1. Markdown 语义块解析

文件被拆分为以下块类型：

- `HEADING` — 标题（`#` 层级 + 归一化文本）
- `PARAGRAPH` — 段落（连续非空文本行）
- `TABLE` — 表格（`|` 语法，含表头和行数）
- `CODE_BLOCK` — 代码块（` ``` ` 围栏 + 语言标识）
- `ADMONITION` — MkDocs 警告块（`!!! type "title"` + 缩进内容）
- `LIST` — 列表（有序/无序，含嵌套项）
- `HORIZONTAL_RULE` — 分隔线（`---`）
- `OTHER` — 其他（HTML 标签等）

每个块生成两个关键属性：
- **signature** — 语言无关的结构签名，用于跨语言弱匹配
- **content_hash** — 归一化内容的 SHA256，用于精确比对内容变化

### 2. 弱匹配对齐

使用 **Needleman-Wunsch 全局序列对齐**，以 `compute_weak_match_score` 作为相似度函数：

- 块类型不同 → 0 分
- HEADING：层级匹配占 0.5，文本存在性奖励 0.1（不惩罚语言差异）
- TABLE：列数一致占 0.6，列头匹配占 0.4
- CODE_BLOCK：语言一致 1.0，不一致 0.5
- 其他类型依结构特征分配权重

Gap penalty 固定为 **0.3**，相似度低于此值的块优先视为插入/删除。

### 3. Git blame 时间比对

对于对齐后的每对块：
- 若 content_hash 相同 → **翻译正常**
- 若 content_hash 不同 → 比较源块和目标块的最新 commit 时间戳
  - 源时间 > 目标时间 → **翻译过时**
  - 源时间 <= 目标时间 → **翻译正常**（同一 commit 或目标更新更晚）

### 4. Tag 停止机制

当指定 `--stop-tag-prefix` 时：
- 查找所有匹配前缀的 tag，按 commit 时间排序（最新的在前）
- 以最新 tag 指向的 commit 为停止点
- 如果某块的所有行都是在 stop-tag **之后**才修改的，则无法确认翻译状态，输出警告

---

## 已知限制

- **Admonition 内部不递归解析**：admonition 块内的表格、代码块等不会作为独立块提取，而是整体归入 admonition。
- **Front matter**：文件顶部的 YAML front matter（`---`）当前会被识别为 `HORIZONTAL_RULE` 或 `OTHER`。
- **段落对齐在长文档中**：纯全局 NW 对齐在段落数量差异较大时可能产生级联错位。后续可考虑基于 HEADING/CODE_BLOCK 等"锚点"分段对齐。
- **代码块内注释**：当前代码块整体计算 content_hash，代码块内的注释变更也会触发"翻译过时"。

---

## 目录结构

```
tools/translate_tracker/
├── core_types.py        # Block, CommitInfo, FileReport, AnalysisReport 等数据类型
├── markdown_parser.py   # Markdown 语义块解析 + 弱匹配签名生成
├── git_tracker.py       # git blame 解析 + Tag 停止机制
├── comparer.py          # Needleman-Wunsch 弱匹配对齐 + diff 计算
├── reporter.py          # 文字化报告生成（text / markdown / json）
├── analyzer.py          # 核心分析引擎（文件发现 → 解析 → 比对 → 汇总）
├── cli.py               # 命令行入口
├── __init__.py
├── __main__.py          # python -m 入口
└── README.md            # 本文件
```

---

## License

与本项目保持一致。
