# 外部文本的规范化闸（单一实现）。
#
# 为什么单独成模块：这道闸必须在**两侧**都用得上——请求侧（用户传来的每个字符串）与
# 输出侧（模型返回的文本）。此前它住在 main.py 里只服务请求侧，于是模型答案里混进一个 NUL
# 就让 `/chat` 返回未声明的 500（PG 文本列存不了 NUL；实测见 tests/test_output_normalization.py）。
# 收成一个共享函数，是为了"再加一处入口时只需要 import 它"，而不是各自 copy 一份规则。
#
# 规则来自 fuzz 修复⑧：
#   NUL(\u0000) 是合法 JSON 值但 PG 文本列存不了 → 直接剔除；
#   孤立代理码点（\ud800-\udfff）编不出 UTF-8 → 换成 U+FFFD；
#   dict/list 递归下钻（JSONB 嵌套，键与值都要处理）。
# 幂等：重复过闸无副作用（所以"两侧都过一遍"是安全的）。
def clean_text(v):
    if isinstance(v, str):
        if "\x00" in v:
            v = v.replace("\x00", "")
        try:
            v.encode("utf-8")
        except UnicodeEncodeError:
            v = "".join("\ufffd" if "\ud800" <= c <= "\udfff" else c for c in v)
        return v
    if isinstance(v, dict):
        return {clean_text(k): clean_text(x) for k, x in v.items()}
    if isinstance(v, list):
        return [clean_text(x) for x in v]
    return v
