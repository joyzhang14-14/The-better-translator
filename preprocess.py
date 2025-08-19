import re
import logging
import asyncio
from typing import Tuple, List

logger = logging.getLogger(__name__)

FSURE_HEAD = "\x1eFSURE\x1e"
FSURE_SEP = "\x1eSEP\x1e"

CUSTOM_EMOJI_RE = re.compile(r"<a?:\w{2,}:\d+>")
UNICODE_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U00002600-\U000026FF\U0001F1E6-\U0001F1FF]+")
EMOJI_PLACEHOLDER = "\x1e{}\x1e"

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
    r"(?:^|[^a-zA-Z\u4e00-\u9fff])包.*?的",
    re.I,
)

# Common traditional Chinese characters that differ from simplified
_TRADITIONAL_CHARS = {
    '繁', '體', '國', '語', '來', '過', '時', '會', '個', '們', '學', '說',
    '話', '長', '開', '關', '經', '對', '現', '發', '這', '樣', '還', '應',
    '當', '從', '後', '處', '見', '間', '問', '題', '實', '點', '條', '機',
    '電', '動', '業', '員', '無', '種', '準', '決', '認', '識', '進', '選',
    '擇', '變', '華', '質', '級', '類', '買', '車', '軟', '體', '網', '絡',
    '係', '統', '計', '劃', '導', '師', '歷', '史', '傳', '統', '優', '勢',
    '創', '辦', '團', '隊', '領', '導', '專', '業', '標', '準', '環', '境',
    '設', '計', '製', '作', '開', '發', '維', '護', '運', '營', '業', '務'
}

def _has_traditional_chinese(text: str) -> bool:
    """Check if text contains traditional Chinese characters"""
    if not text:
        return False
    
    # Check if any character in text is in our traditional characters set
    for char in text:
        if char in _TRADITIONAL_CHARS:
            return True
    return False

async def _convert_traditional_with_deepl(text: str, deepl_client) -> str:
    """Convert traditional Chinese to simplified using DeepL"""
    try:
        # Use DeepL to translate from Traditional Chinese to Simplified Chinese
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: deepl_client.translate_text(text, target_lang="ZH", source_lang="ZH")
        )
        converted = result.text.strip()
        if converted != text:
            logger.info(f"DeepL traditional to simplified: '{text}' → '{converted}'")
        return converted
    except Exception as e:
        logger.warning(f"DeepL traditional conversion failed: {e}, returning original text")
        return text

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
    s_stripped = s.strip()
    m = _PAT_BAO_DE_SENT.search(s_stripped)
    if not m:
        return s
    
    # Extract the matched "包...的" pattern
    matched_text = m.group(0)
    # Find where "包" starts in the matched text (skip prefix punctuation/space)
    bao_start = matched_text.find("包")
    if bao_start == -1:
        return s
    
    # Extract content between 包 and 的
    bao_de_part = matched_text[bao_start:]  # "包...的"
    if len(bao_de_part) < 3:  # At least "包X的"
        return s
    
    core = bao_de_part[1:-1]  # Remove "包" and "的"
    if not core.strip():
        return s
    
    # Get any remaining text after the matched pattern
    remaining = s_stripped[m.end():].strip()
    if remaining:
        return FSURE_HEAD + core + FSURE_SEP + remaining
    else:
        return FSURE_HEAD + core + FSURE_SEP

def extract_emojis(text: str) -> Tuple[str, List[str]]:
    """Extract all emojis from text and replace with placeholders"""
    if not text:
        return text, []
    
    emojis = []
    result = text
    
    # Extract custom Discord emojis first
    custom_matches = list(CUSTOM_EMOJI_RE.finditer(text))
    for i, match in enumerate(custom_matches):
        emoji = match.group(0)
        emojis.append(emoji)
        placeholder = EMOJI_PLACEHOLDER.format(len(emojis) - 1)
        result = result.replace(emoji, placeholder, 1)
    
    # Extract Unicode emojis
    unicode_matches = list(UNICODE_EMOJI_RE.finditer(result))
    for i, match in enumerate(unicode_matches):
        emoji = match.group(0)
        emojis.append(emoji)
        placeholder = EMOJI_PLACEHOLDER.format(len(emojis) - 1)
        result = result.replace(emoji, placeholder, 1)
    
    return result, emojis

def restore_emojis(text: str, emojis: List[str]) -> str:
    """Restore emojis from placeholders back to original text"""
    if not text or not emojis:
        return text
    
    result = text
    for i, emoji in enumerate(emojis):
        placeholder = EMOJI_PLACEHOLDER.format(i)
        result = result.replace(placeholder, emoji)
    
    return result

def has_bao_de_pattern(text: str) -> bool:
    """Check if text contains '包的' pattern that might need GPT judgment"""
    if not text:
        return False
    return bool(_PAT_BAO_DE_SENT.search(text.strip()))

async def preprocess_with_traditional_conversion(text: str, direction: str, deepl_client, skip_bao_de: bool = False) -> str:
    """Preprocess text with traditional Chinese to simplified conversion using DeepL"""
    if not text:
        return text
    
    # Step 1: Convert traditional Chinese to simplified if detected
    if _has_traditional_chinese(text):
        logger.info(f"Traditional Chinese detected in: '{text}'")
        text = await _convert_traditional_with_deepl(text, deepl_client)
    
    # Step 2: Apply normal preprocessing
    return preprocess(text, direction, skip_bao_de)

def preprocess(text: str, direction: str, skip_bao_de: bool = False) -> str:
    s = text or ""
    
    # Handle praise numbers for both directions 
    # (6/666 should become 厉害 regardless of source channel)
    s = _convert_praise_numbers(s)
    
    # Only apply Chinese-specific preprocessing for zh_to_en direction
    if direction == "zh_to_en":
        s = _rewrite_learned_from(s)
        if not skip_bao_de:
            s = _encode_bao_de(s)
        if not s.startswith(FSURE_HEAD):
            s = _which_choose_disamb(s)
    return s

def preprocess_with_emoji_extraction(text: str, direction: str, skip_bao_de: bool = False) -> Tuple[str, List[str]]:
    """Preprocess text with emoji extraction - emojis are extracted before language detection and abbreviation processing"""
    if not text:
        return text, []
    
    # Extract emojis before any processing (including language detection and abbreviations)
    text_without_emojis, extracted_emojis = extract_emojis(text)
    
    # Apply normal preprocessing to text without emojis
    processed_text = preprocess(text_without_emojis, direction, skip_bao_de)
    
    return processed_text, extracted_emojis
