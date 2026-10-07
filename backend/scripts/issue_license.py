#!/usr/bin/env python
# 厂商侧授权签发工具（§D）——**只在厂商机器上运行**，客户部署永不接触私钥。
#
# 用法：
#   # 1. 首次生成密钥对（私钥妥善保管、绝不外发；公钥交客户填 LICENSE_PUBLIC_KEY）
#   python scripts/issue_license.py keygen
#   # 2. 按客户报来的机器指纹签发授权文件
#   python scripts/issue_license.py issue --private-key <私钥> --customer "星辰科技" \
#       --fingerprint <客户在 /admin/license 页复制的指纹> --days 365 --out license.json
#
# 客户侧：把 license.json 放到部署根目录、.env 填 LICENSE_PUBLIC_KEY=<公钥>，重启即生效。
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.license import issue_license, new_keypair  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Umax RAG License 签发工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("keygen", help="生成 Ed25519 密钥对（私钥只留厂商本地）")

    iss = sub.add_parser("issue", help="签发授权文件")
    iss.add_argument("--private-key", required=True, help="厂商私钥 base64（keygen 输出）")
    iss.add_argument("--customer", required=True, help="客户名称（写入授权、管理页可见）")
    iss.add_argument("--fingerprint", required=True,
                     help="客户机器指纹（客户在 管理后台 → 授权 页复制）")
    iss.add_argument("--days", type=int, default=365, help="有效期天数（默认 365）")
    iss.add_argument("--license-key", default=None, help="授权编号（默认自动生成）")
    iss.add_argument("--features", default=None,
                     help='功能集 JSON，如 \'{"max_docs":5000,"max_users":50}\'')
    iss.add_argument("--out", default="license.json", help="输出文件（默认 license.json）")

    args = ap.parse_args()
    if args.cmd == "keygen":
        priv, pub = new_keypair()
        print("私钥（厂商保管，绝不外发）:", priv)
        print("公钥（交客户填 LICENSE_PUBLIC_KEY）:", pub)
        print("\n建议同时存档：私钥丢失将无法为老客户续期。")
        return 0

    features = json.loads(args.features) if args.features else {}
    doc = issue_license(args.private_key, customer=args.customer,
                        machine_fingerprint=args.fingerprint, days=args.days,
                        license_key=args.license_key, features=features)
    out = Path(args.out)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    p = doc["payload"]
    print(f"已签发 → {out}")
    print(f"  授权编号: {p['license_key']}")
    print(f"  客户: {p['customer']}   绑定指纹: {p['machine_fingerprint']}")
    print(f"  有效期至: {p['expires_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
