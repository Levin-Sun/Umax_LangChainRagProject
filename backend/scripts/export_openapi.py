"""重新生成 contracts/openapi.json（唯一合法入口，禁止手改生成物）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import create_app  # noqa: E402

spec = create_app(engine=None).openapi()
out = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"
out.parent.mkdir(parents=True, exist_ok=True)  # contracts/ 被 clean 掉时也能重建目录再导出
out.write_text(json.dumps(spec, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
               encoding="utf-8")
print(f"已导出 {out}（{len(spec['paths'])} 个路径）")
