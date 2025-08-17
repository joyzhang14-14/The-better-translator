import re

FSURE_HEAD = "\x1eFSURE\x1e"
FSURE_SEP = "\x1eSEP\x1e"

_PUNCT = r"，。！？；：、,.!?;:\(\)\[\]\{\}《》〈〉「」『』【】<>…～~\s"
_Q_ANY = r"(?:哪(?:个|些|儿|边|路|位|只|队)?|谁|什么|啥|哪里|哪儿)"
_VERB_OR = r"(?:选择|挑选|选|挑|买|用|取|点|订|拿|出|出装|带|走|玩|上|开|配|搭|切|切换|锁|锁定|补位|Ban|ban|Pick|pick|buy|use|take|choose|select|lock|ban|pick|fill)"
_PAT_WHICH_CHOOSE = re.compile(rf"({_Q_ANY})([^，。！？,.!?；;]{{1,8}}?)({_VERB_OR})({_Q_ANY})", re.I)

_HAS_FOR_PURPOSE = re.compile(r"[为给供][^，。！？,.!?]{0,6}做的")
_PAT_LEARN_ME = re.compile(r"(?:我|我们)?学(.{1,18}?)(?:搞的|做的|整的|出来的|来的)", re.I)
_PAT_LEARN_FROM = re.compile(r"(?:跟|向|从)(.{1,18}?)(?:学(?:的)?)(?:搞的|做的|整的|出来的|来的)", re.I)
_PAT_MODEL_ON = re.compile(r"(?:照着|照|按|依照|参考|借鉴|仿照|模仿)(.{1,18}?)(?:搞的|做的|整的|出来的|来的)", re.I)
_PAT_COPY = re.compile(r"(?:抄自|抄)(.{1,18}?)(?:的(?:功能|做法|方案|点子|思路)?)?", re.I)

_PAT_BAO_DE_SENT = re.compile(
    r"^(?:\s*)包(.*?)的",
    re.I,
)

def _rewrite_learned_from(s: str) -> str:
    if not s or _HAS_FOR_PURPOSE.search(s):
        return s
    def fix(m: re.Match) -> str:
        src = m.group(1).strip()
        return f"仿照{src}做的"
    for pat in (_PAT_LEARN_ME, _PAT_LEARN_FROM, _PAT_MODEL_ON):
        s2 = pat.sub(fix, s)
        if s2 != s:
            s = s2
    def fix2(m: re.Match) -> str:
        src = (m.group(1) or "").strip()
        return f"参考{src}做的" if src else "参考它做的"
    s2 = _PAT_COPY.sub(fix2, s)
    if s2 != s:
        s = s2
    return s


def _which_choose_disamb(s: str) -> str:
    if len(s) > 24:
        return s
    def fix(m: re.Match) -> str:
        return f"{m.group(1)}{m.group(2)}就{m.group(3)}{m.group(4)}"
    return _PAT_WHICH_CHOOSE.sub(fix, s)

def _convert_praise_numbers(s: str) -> str:
    """Convert standalone 6 or 666 to 厉害 (awesome)"""
    s = s.strip()
    if s == "6" or s == "666":
        return "厉害"
    return s

def _encode_bao_de(s: str) -> str:
    m = _PAT_BAO_DE_SENT.match(s.strip())
    if not m:
        return s
    core = (m.group(1) or "").strip()
    if not core:
        return s
    # Extract content between 包 and 的, will add "for sure" after translation
    # Get any remaining text after "的"
    remaining = s.strip()[m.end():].strip()
    if remaining:
        return FSURE_HEAD + core + FSURE_SEP + remaining
    else:
        return FSURE_HEAD + core + FSURE_SEP

def preprocess(text: str, direction: str) -> str:
    s = text or ""
    
    # Handle praise numbers for both directions 
    # (6/666 should become 厉害 regardless of source channel)
    s = _convert_praise_numbers(s)
    
    # Only apply Chinese-specific preprocessing for zh_to_en direction
    if direction == "zh_to_en":
        s = _rewrite_learned_from(s)
        s = _encode_bao_de(s)
        if not s.startswith(FSURE_HEAD):
            s = _which_choose_disamb(s)
    return s
