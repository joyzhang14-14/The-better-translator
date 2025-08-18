import re
import json
import logging
from typing import Dict

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://\S+")
CUSTOM_EMOJI_RE = re.compile(r"<a?:\w{2,}:\d+>")
UNICODE_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U00002600-\U000026FF\U0001F1E6-\U0001F1FF]+")
PUNCT_GAP_RE = re.compile(r"[\s\W_]+", re.UNICODE)

def build_jump_url(gid: int, cid: int, mid: int) -> str:
    return f"https://discord.com/channels/{gid}/{cid}/{mid}"

def is_image_attachment(att) -> bool:
    if att.content_type and att.content_type.startswith("image/"):
        return True
    name = (att.filename or "").lower()
    return any(name.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))

def strip_banner(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i].lstrip().startswith(">"):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    body = lines[i:]
    return "\n".join(body).strip()

def _merge_default(mapping: Dict, gid: str) -> Dict:
    base = mapping.get("default", {})
    out = dict(base)
    out.update(mapping.get(gid, {}))
    return out

def _normalize_wrapped_urls(s: str) -> str:
    if not s:
        return s
    return re.sub(r"<+\s*(https?://[^>\s]+)\s*>+", r"<\1>", s)

def _suppress_url_embeds(s: str) -> str:
    def _wrap(m: re.Match) -> str:
        u = m.group(0)
        if u.startswith("<") and u.endswith(">"):
            return u
        return f"<{u}>"
    return URL_RE.sub(_wrap, s or "")

def _shorten(s: str, n: int) -> str:
    if n and n > 0 and len(s) > n:
        return s[: n - 1].rstrip() + "…"
    return s

def _delink_for_reply(s: str) -> str:
    if not s:
        return s
    s = _normalize_wrapped_urls(s)
    s = re.sub(r"<\s*(https?://[^>\s]+)\s*>", r"\1", s)
    s = re.sub(r"(?i)\bhttps?://", lambda m: m.group(0)[0] + "\u200b" + m.group(0)[1:], s)
    s = re.sub(r"(?i)\bwww\.", "w\u200bbw.", s)
    return s

def _is_command_text(gid: str, s: str, passthrough_cfg: dict) -> bool:
    if not s:
        return False
    t = s.strip()
    
    if t.startswith("!"):
        return True
    
    cmds = _merge_default(passthrough_cfg, gid).get("commands", [])
    for c in cmds:
        if t.lower().startswith(c.lower()):
            return True
    return False

def _is_filler(s: str, gid: str, passthrough_cfg: dict) -> bool:
    if not s:
        return False
    base = _merge_default(passthrough_cfg, gid).get("fillers", [])
    t = CUSTOM_EMOJI_RE.sub("", s)
    t = UNICODE_EMOJI_RE.sub("", t)
    t = t.strip().lower()
    if not t:
        return True
    if any(t == f.lower() for f in base):
        return True
    if re.fullmatch(r"(e?hm+|e+m+h+|em+|oh+|ah+|uh+h*|h+|w+|…+|\.)", t):
        return True
    return False

def _apply_abbreviations(text: str, gid: str, guild_abbrs: dict) -> str:
    d = _merge_default(guild_abbrs, gid)
    if not d:
        return text or ""
    s = text or ""

    def is_url_context(idx: int) -> bool:
        left = max(0, idx - 8)
        right = min(len(s), idx + 16)
        seg = s[left:right]
        return bool(URL_RE.search(seg))

    for k, v in sorted(d.items(), key=lambda kv: len(kv[0]), reverse=True):
        if not k:
            continue
        end_zh = k.endswith("的") or v.endswith("的") or bool(re.search(r"[\u4e00-\u9fff]", k + v))
        if end_zh:
            pat = re.compile(re.escape(k))
            def rep(m: re.Match):
                i = m.end()
                if i < len(s):
                    nxt = s[i]
                    if URL_RE.match(s[i:]):
                        return m.group(0)
                    if re.match(r"[A-Za-z0-9_]", nxt):
                        return m.group(0)
                return v
            s = pat.sub(rep, s)
        else:
            pat = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(k)}(?![A-Za-z0-9_])")
            def rep2(m: re.Match):
                return v if not is_url_context(m.start()) else m.group(0)
            s = pat.sub(rep2, s)
    return s

def _load_json_or(path: str, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read().strip()
            return json.loads(txt) if txt else fallback
    except Exception:
        return fallback