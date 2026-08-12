# Vision Benchmark 套件（P5b 骨架）

vision 链路（多图通道 + Vision Harness）的回归套件。独立小套件 + 独立语料
`benchmark/fixtures/llm_corpus/vision.jsonl`，不混入 create/modify 套件。

## 任务清单

| id | 任务 | 参考图 | 说明 |
|----|------|--------|------|
| V01 | 海棠纹漏窗（CREATE/IMAGE） | `benchmark/fixtures/vision/begonia_lattice.jpg` | 井字格底 + 四瓣海棠单元，源 `materials/patterns/lanyuan-62.jpg` |
| V02 | 冰裂纹漏窗（CREATE/IMAGE） | `benchmark/fixtures/vision/ice_crack_lattice.jpg` | 方洞冰裂正面，源 `materials/patterns/lanyuan-44.jpg` |
| V03 | 圆洞冰裂纹漏窗（CREATE/IMAGE） | `benchmark/fixtures/vision/round_window_ice_crack.jpg` | 拙政园月洞 + 冰裂窗芯，源 `materials/patterns/round-window-humble.jpg` |

## 录制（未执行——消耗真实 token，由监控方复核后执行）

```bash
# 真实 LLM 录制一次（vision 调用走 generate / generate_with_image，均可录制）
python -m benchmark.runner --suite benchmark/tasks/vision/ --mode auto --jobs 1 \
  --llm-record benchmark/fixtures/llm_corpus/vision.jsonl

# 之后离线确定性回放
python -m benchmark.runner --suite benchmark/tasks/vision/ --mode mock --jobs 1 \
  --llm-replay benchmark/fixtures/llm_corpus/vision.jsonl
```

注意：
- 录制命令用 `--jobs 1`（vision 套件图多、语料小，串行最稳；与 create/modify 的
  4 worker 并行规范不同，因为本套件目前只有 3 题）。
- vision 语料 key 与 create/modify 同构（messages + kwargs 的 sha256）；未命中
  报错并提示重录，与主套件同一纪律。
- 任务 description 变更 = 送给 LLM 的指令变更 → 必须重录（黄金语料规范）。

## fixture 规范

- 图片放 `benchmark/fixtures/vision/`，来源 `materials/patterns/`（Wikimedia
  Commons 自由授权，商用需注意各自授权条款，见源目录 README）。
- **超 500KB 的先降采样到 1568px 长边再入库**（本套件当前 3 张均为 ~960px
  缩略图、<500KB，直接拷贝）。降采样命令：

```bash
python - <<'PY'
from PIL import Image
im = Image.open("materials/patterns/xxx.jpg")
if max(im.size) > 1568:
    im.thumbnail((1568, 1568))
    im.save("benchmark/fixtures/vision/xxx.jpg", "JPEG", quality=85)
PY
```

- harness 侧（P5a 预处理）对入库图还会统一压到 1568px 长边，fixture 内卷尺寸
  只是为了仓库体积与入库纪律。

## 注意

- `benchmark/baseline.json` 不含 vision 套件；`check_baseline` 只跑 create +
  modify，本套件不影响基线门禁。
- 当前为骨架：任务/语料/断言随 P5c/P5d 演进（critic 校验、字段置信度入断言）。
- **P5c（2026-08-12）prompt 变更 → vision.jsonl 已过期**：extract_prompt 追加了
  字段级置信度要求（输出信封 `{fields, confidence, raw_description}`），且
  CREATE/IMAGE + critic_checks 非空时每图多一次 critic 调用——语料必然 miss
  （回放报"未命中"是特性：证明悄悄改 prompt 会被拦住）。合并后由监控方用
  `--mode auto --jobs 1 --llm-record` 重录（见上），录制命令不变。

## 次轮录制结果（2026-08-12，P5c critic 链路，kimi-k2.6，14 条语料）

1/3 PASS（回放闭环验证通过，失败原因逐字复现）：
- V01 海棠纹 ✅（首轮 ❌ transform 不平衡 → 本轮通过，critic 核对后改善）
- V02 冰裂纹 ❌：criteria/contract/编译全过，挂在 static_pass（静态检查）
- V03 圆洞冰裂 ❌：2d.gdl 缺失/空、缺 CIRCLE、transform stack 未闭合
  （5 push / 4 pop）

## 首轮录制结果（2026-08-12，kimi-k2.6，13 条语料）

0/3 PASS——回放已逐字复现失败原因（闭环验证通过），失败全部是生成质量问题
而非链路问题：V01/V03 transform stack 不平衡（push≠pop）、V03 缺 CIRCLE
命令、V02/V03 大量 derived_var_not_in_master 告警。这是 k2.6 在漏窗几何上的
真实水平基线，P5c critic 与漏窗项目的改进以此为对照。

录制前置修复（同 commit）：vision 调用硬编码 temperature=0.1 与 kimi 端点
约束（仅 0.6/1）冲突导致提取全部 400 降级——harness._schema_plan 与
analyze_reference_image 两处改为不传 temperature，由 LLMAdapter
_effective_temperature 按 provider 条目级配置决定。
