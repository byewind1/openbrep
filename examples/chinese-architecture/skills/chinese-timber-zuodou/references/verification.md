# 坐斗公共模板验证

日期：2026-10-09。验证对象是本包 `assets/zuodou/`，不是旧项目的参数快照。
机器结果及各源文件 SHA-256 见 [verification.json](verification.json)。

## 已执行

| 项目 | 结果 |
|---|---|
| Skill frontmatter / 命名检查 | skill-creator quick_validate 通过 |
| HSF 静态检查 | 通过，无 warning |
| 默认尺寸 0.396 × 0.396 × 0.300 m | 本地几何检查通过 |
| 非正方形 0.600 × 0.400 × 0.450 m | 本地几何检查通过 |
| 缩小尺寸 0.200 × 0.300 × 0.150 m | 本地几何检查通过，未出现隐式最小尺寸放大 |
| 安装为项目 Skill 后的加载 | 坐斗修改指令命中并注入核心公式 |
| Archicad 29 LP_XMLConverter | 临时副本编译通过，exit 0，无 stdout/stderr 诊断 |
| 仓库 Python 全套测试 | 3764 passed，11 skipped，102 subtests passed；两条终端 getpass warning |

几何检查来自 OpenBrep 本地 GDL 预览器：

- A/B/ZZYZX 与外包络相符；一棱台、一斗身、四耳、两低块，共八个 mesh。
- 棱台存在四个斜面方向，1/4、1/2、3/4 高度的截面满足连续线性放大关系。
- 棱台顶部与斗身衔接，耳与低块都落在斗身顶面。
- 前后低块与对应耳的内侧共面，沿 X 的两端接触斗耳，低块厚度和高度符合案例比例。

检查只针对这个模板的网格顺序和几何约定。改变实体分解后应更新检查器；
仅用不同代码得到同一个形状，不能因为 mesh 数量不同就判定为建模失败。

## 重现

从 OpenBrep 仓库根目录执行，需本地 Python 环境已安装项目依赖：

```sh
PYTHONPATH=. python examples/chinese-architecture/skills/chinese-timber-zuodou/scripts/verify_example.py --compile
```

未安装 LP_XMLConverter 时去掉 `--compile`，结果会明确标记 compile 为 `not_run`。
转换在临时 HSF 副本中完成，不改仓库模板，不保存二进制 GSM，不调用 LLM。

## 未执行 / 不构成的证明

没有本公共模板在 Archicad 宿主内执行、放置与视觉签认的记录；2D PROJECT2 的宿主显示也未验收。
尚无独立 Agent 从陌生参考图完成建模的效果评测。本地检查和编译不证明历史尺度、
单一实体拓扑、木工构造或结构性能正确。
