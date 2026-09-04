"""质量账本（G0/G1，observer-only）。

每次 pipeline 任务生成一份不可变质量档案（QualityRecord），落到
``<project>/.openbrep/quality/runs/<run_id>.json``。

纪律（设计稿 v2 §3.2/§4/§5 G0+G1）：
- 纯观测：不出综合分、不进任何 prompt、不改任何判定；
- 单点写入：pipeline finalizer 统一写一次，best-effort，写失败只 warning；
- 质量评估只复用已算过的 verification/preview 结果，绝不重跑；
- benchmark 回放经 ``quality_ledger_enabled=False`` 显式关闭，零污染。
"""

from openbrep.quality.schema import OUTCOMES, SCHEMA_VERSION, QualityRecord

__all__ = ["OUTCOMES", "SCHEMA_VERSION", "QualityRecord"]
