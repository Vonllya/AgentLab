# 生成资产与冻结实例格式

[返回首页](../README.md) · [任务包格式](TASK_PACKAGES.md) · [生成流程](FAULT_FIRST.md)

本文面向需要理解或维护生成任务的开发者。当前资产协议为 `agentlab-generated-json-v1`；具体约束以[基础 Schema](../backend/generation_schema.py)、[故障优先流程](../backend/generation_fault_first.py)和[精简审查](../backend/generation_lean.py)为准。

## 需求与公开规范

需求包含自然语言说明，以及可选的难度、场景偏好和预计时长。构建阶段同时输出项目与规范；当前新任务没有单独的技术契约确认步骤。只有用户目标本身需要澄清时才等待用户补充。

| 字段 | 内容 |
| --- | --- |
| `title`、`scenario`、`symptom` | 标题、场景与公开症状 |
| `capabilities`、`minutes` | 能力标签与预计时长；时长属于估计 |
| `input_schema`、`output_schema` | `scenario(data)` 的 JSON 输入输出格式 |
| `behaviors` | 稳定行为 ID 到正常行为说明的映射 |
| `constraints`、`input_domain`、`exclusions` | 实现约束、有效输入与不考察的情况 |
| `files`、`simulation` | 业务模块清单与模拟范围 |

公开规范描述正确行为，私有故障设计单独保存。获准修订产生新规范版本，受影响的评测与验证必须更新，不能继续使用旧摘要的批准。

JSON Schema 支持 `type`、`properties`、`required`、`additionalProperties`、`items`、`enum` 和 `description`。`additionalProperties` 仅接受布尔值。不支持的关键字会被拒绝，不会默默忽略。

## 项目文件与接口

- 每题包含 2–5 个扁平 Python 业务模块，必须含 `app.py`。
- 模型提供文件名到源码的映射；各实现版本遵循同一文件清单。
- 文件名必须符合受控命名规则，禁止路径穿越、保留模块名与任意目录。
- 单文件最多 24,000 UTF-8 字节，每个实现版本合计最多 60,000 字节。
- 仅使用固定 Python 标准库环境；模型不能指定镜像、安装依赖或提交宿主执行脚本。

平台生成只读 `solution.py`：

```python
from app import scenario
```

`scenario(data)` 直接返回符合输出 Schema 的 Python 值，由平台序列化一次。对象或数组不应预先 `json.dumps` 为字符串。

每次调用都在独立受限容器内执行。需要本地存储的场景应在一次调用内完成动作序列，不得依赖其他用例留下的文件。跨进程持久化是否被验证，需要专门的可观察证据，不能由函数命名推断。

## 测试与故障检查

独立评测提交结构化数据，不提交供宿主执行的 Python 评测器。

| 用例字段 | 含义 |
| --- | --- |
| `id` | 用例标识，最多 96 字符 |
| `visibility` | `public` 或 `hidden` |
| `group` | 目标故障场景或正常回归；当前流程还会依据故障影响资产核定分类 |
| `covers` | 引用公开行为 ID；标签本身不证明覆盖充分 |
| `input` | 合法输入 |
| `expected` | 依据公开规范推导的完整正确输出 |

每套评测包含 4–10 个用例，要求公开、隐藏目标场景和正常回归。输入与正确期望必须符合 Schema。初次评测不读取代码或实际输出。

故障复现资产与正确期望分开保存：包含预设错误断言、逐场景影响范围和依据。它们属于作者私有材料，不能写入公开训练用例。运行错误不能充当预设错误行为。

补测使用绑定原评测摘要的 `base_hash` 与 `added_cases`。程序合并增量并保留旧用例；不能通过补测悄悄删除检查或更改既有正确期望。

## 修复资产与审计

故障构造校正与参考修复使用绑定代码摘要的局部补丁。指定文件中的 `before` 必须唯一匹配，再应用 `after`；程序检查范围和版本，拒绝过期候选。构建不能借代码补丁修改独立测试。

每次模型调用保存输入资产、可见响应、校验结果与脱敏元数据。尝试记录包含阶段、角色、输入输出摘要、预算记账、供应商用量和错误类别；缺失用量标为未知，不保存隐藏推理内容。

```text
数据目录/
  app.sqlite3
  generation/<job_id>/
    call-<call_id>/
      input.json
      response.txt
      output.json          # 通过解析与校验时保存
    …                      # 各阶段资产、候选与执行文件
```

验证矩阵关联版本、用例、执行 ID、代码快照、环境、耗时与结果。模型回复“完成”只代表候选返回，不能代替执行通过。

## 发布包

发布前必须完成验证与教学阶段，由作者确认当前 `review_digest` 并填写审核依据。程序在发布时重算摘要，资产发生变化时拒绝沿用旧批准。

```text
数据目录/published/gen_<job_id>/<version>/
  manifest.json
  initial/                 # 故障版业务模块与只读入口
  public_cases.json
  public_test.py           # 说明入口；判分由平台解释器执行
  private/
    normal/
    reference/
    hidden_cases.json
    hints.json
    author.json            # 作者资产与生成审计材料
    freeze.json            # 冻结清单与批准绑定
```

manifest 保存公开规范、来源、版本、文件权限与运行协议。`freeze.json` 保存文件哈希、规范摘要、模型模式、验证矩阵、Docker 环境标识及审核记录。

同一发布请求不会创建重复实例。后续修订使用新版本；训练会话仍引用创建时的版本和冻结资产，不重新调用模型生成题目。

## 兼容性与信任范围

新任务在批次中保存生成策略与预算。历史记录按各自协议读取，不强制迁移旧快照、旧评测或报告。不要直接修改已发布目录、冻结清单或数据库来“修复”历史任务。

作者私有视图与学习者文件 API 分离，但本地所有者能够查看全部源码和数据。模型生成的测试标准仍可能有误，发布前需要人工核对；自动验证只对本轮资产与检查负责。
