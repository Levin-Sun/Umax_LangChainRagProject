# 契约 fuzz 的前提：错误码必须出现在 spec 里，否则 schemathesis 把合法 404 判成违约
from pathlib import Path

import json

SPEC = json.loads((Path(__file__).resolve().parents[2] / "contracts/openapi.json")
                  .read_text(encoding="utf-8"))

EXPECTED = {
    ("/api/v1/kb", "post"): {"401"},
    ("/api/v1/kb/{kb_id}/documents", "post"): {"404", "415", "401"},
    ("/api/v1/documents/{doc_id}", "get"): {"404"},
    ("/api/v1/documents/{doc_id}", "patch"): {"404", "401"},
    ("/api/v1/documents/{doc_id}/reprocess", "post"): {"404", "401"},
    ("/api/v1/documents/{doc_id}/chunks", "get"): {"404"},
    ("/api/v1/conversations/{conv_id}/messages", "get"): {"404"},
    ("/api/v1/models", "get"): {"401"},
    ("/api/v1/models", "post"): {"400", "503", "401"},
    ("/api/v1/models/{model_id}", "patch"): {"400", "404", "503", "401"},
    ("/api/v1/models/{model_id}", "delete"): {"503", "401"},
    ("/api/v1/usage/summary", "get"): {"401"},
    ("/api/v1/admin/login", "post"): {"401"},
    ("/api/v1/admin/logout", "post"): set(),
}


def test_error_codes_declared():
    for (path, method), codes in EXPECTED.items():
        declared = set(SPEC["paths"][path][method]["responses"])
        assert codes <= declared, f"{method.upper()} {path} 缺 {codes - declared}"


# 管理面 = 401 声明的全集：新增管理类端点忘挂守护/忘入约时在此曝光
def test_admin_surface_is_exactly_401_declared():
    guarded = {(p, m) for p, ms in SPEC["paths"].items()
               for m, r in ms.items() if "401" in r["responses"]}
    assert guarded == set(EXPECTED) - {
        ("/api/v1/documents/{doc_id}", "get"),
        ("/api/v1/documents/{doc_id}/chunks", "get"),
        ("/api/v1/conversations/{conv_id}/messages", "get"),
        ("/api/v1/kb/{kb_id}/documents", "get"),
        ("/api/v1/admin/logout", "post"),
    }
