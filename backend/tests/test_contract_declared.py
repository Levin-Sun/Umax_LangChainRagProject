# 契约 fuzz 的前提：错误码必须出现在 spec 里，否则 schemathesis 把合法 404 判成违约
from pathlib import Path

import json

SPEC = json.loads((Path(__file__).resolve().parents[2] / "contracts/openapi.json")
                  .read_text(encoding="utf-8"))

EXPECTED = {
    ("/api/v1/kb/{kb_id}/documents", "post"): {"404", "415"},
    ("/api/v1/documents/{doc_id}", "get"): {"404"},
    ("/api/v1/documents/{doc_id}", "patch"): {"404"},
    ("/api/v1/documents/{doc_id}/reprocess", "post"): {"404"},
    ("/api/v1/documents/{doc_id}/chunks", "get"): {"404"},
    ("/api/v1/conversations/{conv_id}/messages", "get"): {"404"},
    ("/api/v1/models", "post"): {"400", "503"},
    ("/api/v1/models/{model_id}", "patch"): {"400", "404", "503"},
    ("/api/v1/models/{model_id}", "delete"): {"503"},
}


def test_error_codes_declared():
    for (path, method), codes in EXPECTED.items():
        declared = set(SPEC["paths"][path][method]["responses"])
        assert codes <= declared, f"{method.upper()} {path} 缺 {codes - declared}"
