# OpenBrep Pro 商业化 V1 规格

## 1. 授权码格式

- **格式设计**：`OBR-XXXX-XXXX-XXXX`（字母数字混合，可读性好）
- **生成算法**：基于 `buyer_id + 到期时间 + 随机盐` 的 `HMAC-SHA256` 截断
- **字段**：`buyer_id`, `email`, `plan(annual/lifetime)`, `expire_date`, `issued_at`

### Python 示例（授权码生成）

```python
import hmac
import hashlib
import secrets
import string
from datetime import datetime

SECRET = b"replace-with-your-secret-key"


def to_base36(n: int) -> str:
    chars = string.digits + string.ascii_uppercase
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(chars[r])
    return "".join(reversed(out))


def gen_license_code(buyer_id: str, expire_date: str) -> str:
    salt = secrets.token_hex(4)  # 随机盐
    payload = f"{buyer_id}|{expire_date}|{salt}".encode("utf-8")
    digest = hmac.new(SECRET, payload, hashlib.sha256).hexdigest()[:12].upper()
    n = int(digest, 16)
    token = to_base36(n).zfill(12)[:12]
    return f"OBR-{token[0:4]}-{token[4:8]}-{token[8:12]}"


record = {
    "buyer_id": "B001",
    "email": "buyer@example.com",
    "plan": "annual",
    "expire_date": "2027-02-28",
    "issued_at": datetime.now().isoformat(timespec="seconds"),
}
record["license_code"] = gen_license_code(record["buyer_id"], record["expire_date"])
print(record)
```

---

## 2. 知识包格式（.obrk）

- `.obrk` 本质是 zip，内含：
  - `manifest.json`（`buyer_id`, `email`, `plan`, `expire_date`, `signature`）
  - `docs/*.md`（Pro 知识库文件）
  - `signature.sig`（对 `manifest.json` 内容的 RSA 签名）
- `buyer_id` 水印：每个 `.md` 文件末尾插入隐形标记行
  - 格式：`<!-- obr:buyer:{buyer_id}:{checksum} -->`

### 建议目录结构

```text
pro_package.obrk
├── manifest.json
├── signature.sig
└── docs/
    ├── GDL_Advanced_01.md
    ├── GDL_Advanced_02.md
    └── ...
```

---

## 3. 签名流程

1. 用私钥（本地保存）对 `manifest.json` 做 `RSA-SHA256` 签名
2. 公钥内嵌到 `openbrep` 代码里（用于客户端验签）
3. 打包脚本：`scripts/pack_pro.py`
   - 参数：`--buyer-id --email --plan --expire`

### Python 示例（签名/验签）

```python
import json
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# 签名
private_key = serialization.load_pem_private_key(
    Path("keys/pro_private.pem").read_bytes(),
    password=None,
)
manifest_bytes = Path("manifest.json").read_bytes()
signature = private_key.sign(
    manifest_bytes,
    padding.PKCS1v15(),
    hashes.SHA256(),
)
Path("signature.sig").write_bytes(signature)

# 验签
public_key = serialization.load_pem_public_key(
    Path("keys/pro_public.pem").read_bytes()
)
public_key.verify(
    Path("signature.sig").read_bytes(),
    manifest_bytes,
    padding.PKCS1v15(),
    hashes.SHA256(),
)
```

---

## 4. 客户端验证流程（ui/app.py 侧边栏）

- 入口：「🔐 Pro 授权」expander
- 步骤：
  1. 输入授权码
  2. 上传 `.obrk`
  3. 验签
  4. 解压到 `knowledge/ccgdl_dev_doc/`
  5. 显示 Pro 已激活
- 验签失败提示：
  - 「授权包验证失败，请联系 byewind@xxx 获取支持」

### 流程要点

- 授权码格式和有效期先检查
- `manifest.json` 与 `signature.sig` 必须同时存在
- 验签通过后再写入本地目录
- 激活状态落盘（例如 `~/.openbrep/license_v1.json`）

---

## 5. 发货后台（本地脚本，无需服务器）

- `scripts/gen_license.py`：生成授权码，输出到 `licenses.csv`
- `scripts/pack_pro.py`：生成带水印的 `.obrk` 包
- 记录字段：`buyer_id`, `email`, `plan`, `issued_at`, `expire_date`, `status(active/revoked)`

### Python 示例（CSV 记录）

```python
import csv
from datetime import datetime
from pathlib import Path

fields = ["buyer_id", "email", "plan", "issued_at", "expire_date", "status", "license_code"]
row = {
    "buyer_id": "B001",
    "email": "buyer@example.com",
    "plan": "annual",
    "issued_at": datetime.now().isoformat(timespec="seconds"),
    "expire_date": "2027-02-28",
    "status": "active",
    "license_code": "OBR-AB12-CD34-EF56",
}

csv_path = Path("licenses.csv")
exists = csv_path.exists()
with csv_path.open("a", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    if not exists:
        w.writeheader()
    w.writerow(row)
```

---

## 6. 定价策略

- 年费：**¥299/年**（绑定 1 台设备，可换机 1 次/年）
- 知识库随版本持续更新
- 不做永久买断

---

## 7. 购买页文案（微信/表单用）

### 推荐短文案

**OpenBrep Pro（¥299/年）**

- 提供高级 GDL 知识库与持续更新
- Free 版可用基础能力；Pro 版适合高频、复杂对象开发
- 购买后你将收到：授权码 + Pro 知识包（.obrk）

**购买流程**
1. 提交购买信息（邮箱/微信）
2. 完成付款
3. 收到授权码与知识包
4. 在 OpenBrep 内导入并激活

**许可协议关键条款**
- 授权仅限购买者本人使用
- 禁止转售、分享、二次分发知识包
- 发现泄露将撤销授权并保留追责权利

---

## 8. 实施优先级

- **P0**：`pack_pro.py + gen_license.py`（能发货）
- **P1**：侧边栏授权入口（用户能激活）
- **P2**：水印追踪机制

---

## 附：脚本参数约定（V1）

### `scripts/gen_license.py`

```bash
python scripts/gen_license.py \
  --buyer-id B001 \
  --email buyer@example.com \
  --plan annual \
  --expire 2027-02-28
```

### `scripts/pack_pro.py`

```bash
python scripts/pack_pro.py \
  --buyer-id B001 \
  --email buyer@example.com \
  --plan annual \
  --expire 2027-02-28
```
