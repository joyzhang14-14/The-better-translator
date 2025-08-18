import logging
import re

logger = logging.getLogger(__name__)

CUSTOM_EMOJI_RE = re.compile(r"<a?:\w{2,}>:\d+>")
UNICODE_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U00002600-\U000026FF\U0001F1E6-\U0001F1FF]+")

class GPTHandler:
    def __init__(self, openai_client):
        self.openai_client = openai_client

    async def detect_language(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return "meaningless"
        
        t2 = CUSTOM_EMOJI_RE.sub("", t)
        t2 = UNICODE_EMOJI_RE.sub("", t2)
        t2 = re.sub(r"(e?m+)+", "em", t2, flags=re.IGNORECASE)
        zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
        en_count = len(re.findall(r"[A-Za-z]", t2))
        
        if zh_count and not en_count:
            return "Chinese"
        if en_count and not zh_count:
            return "English"
        
        if zh_count and en_count:
            return await self._ai_detect_language(t)
        
        return "meaningless"

    async def _ai_detect_language(self, text: str) -> str:
        sys = (
            "Analyze the text and determine the PRIMARY language. "
            "Consider which language carries the main meaning. "
            "Output exactly one word: Chinese, English, or meaningless."
        )
        usr = f"Text: {text}"
        try:
            if not self.openai_client:
                t2 = CUSTOM_EMOJI_RE.sub("", text)
                zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
                en_count = len(re.findall(r"[A-Za-z]", t2))
                return "Chinese" if zh_count >= en_count else "English"
                
            r = await self.openai_client.chat.completions.create(
                model="gpt-5-mini",
                messages=[{"role":"system","content":sys},{"role":"user","content":usr}],
                max_completion_tokens=5
            )
            result = (r.choices[0].message.content or "").strip().lower()
            if "chinese" in result:
                return "Chinese"
            if "english" in result:
                return "English"
            t2 = CUSTOM_EMOJI_RE.sub("", text)
            zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
            en_count = len(re.findall(r"[A-Za-z]", t2))
            return "Chinese" if zh_count >= en_count else "English"
        except Exception as e:
            logger.error(f"AI language detection failed: {e}")
            t2 = CUSTOM_EMOJI_RE.sub("", text)
            zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
            en_count = len(re.findall(r"[A-Za-z]", t2))
            return "Chinese" if zh_count >= en_count else "English"

    async def apply_star_patch(self, prev_text: str, patch: str) -> str:
        lang = await self.detect_language(prev_text)
        logger.info(f"DEBUG: Star patch - lang: {lang}, prev: '{prev_text}', patch: '{patch}'")
        
        if lang == "Chinese":
            sys = (
                "用户发送了两条消息：第一条是完整句子，第二条以*结尾是补丁。"
                "你需要将补丁内容智能地合并到原句中，形成一个完整的新句子。"
                "规则：\n"
                "1. 如果补丁是替换词，就替换原句中最相关的部分\n"
                "2. 如果补丁是补充词，就添加到原句合适的位置\n"
                "3. 保持语法正确和语义连贯\n"
                "4. 只返回合并后的完整句子，不要解释"
            )
            usr = f"原句：{prev_text}\n补丁：{patch}\n\n请返回合并后的句子："
        else:
            sys = (
                "User sent two messages: first is a complete sentence, second ends with * as a patch. "
                "You need to intelligently merge the patch content into the original sentence to form one complete new sentence.\n"
                "Rules:\n"
                "1. If patch is a replacement word, replace the most relevant part in original\n"
                "2. If patch is additional word, add it to appropriate position in original\n"
                "3. Keep grammar correct and meaning coherent\n"
                "4. Return only the merged complete sentence, no explanation"
            )
            usr = f"ORIGINAL: {prev_text}\nPATCH: {patch}\n\nReturn merged sentence:"
        
        try:
            if not self.openai_client:
                logger.info(f"DEBUG: No OpenAI client, using fallback")
                return f"{prev_text} {patch}".strip()
            
            logger.info(f"DEBUG: Calling OpenAI for star patch merge...")
            r = await self.openai_client.chat.completions.create(
                model="gpt-5-mini",
                messages=[{"role":"system","content":sys},{"role":"user","content":usr}]
            )
            logger.info(f"DEBUG: OpenAI response received")
            result = (r.choices[0].message.content or "").strip()
            logger.info(f"DEBUG: Star patch result: '{result}'")
            return result or prev_text
        except Exception as e:
            logger.error(f"OpenAI star patch failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            fallback_result = f"{prev_text} {patch}".strip()
            logger.info(f"DEBUG: Using fallback result: '{fallback_result}'")
            return fallback_result

    async def judge_bao_de(self, text: str) -> str:
        sys = (
            "You are a Chinese to English translator. Analyze the Chinese text and determine if any instance of '包的' "
            "means 'for sure' (expressing certainty/guarantee) rather than referring to a physical bag. "
            "Common patterns that mean 'for sure': 包赢的, 包过的, 包好的, 包准的, 包成的, etc. "
            "If '包的' expresses certainty/guarantee, translate the entire sentence naturally. "
            "If '包的' refers to a bag/package or if there's no clear '包的' pattern, return 'NOT_FOR_SURE' exactly. "
            "Examples: '包赢的' = guaranteed win, '包好的' = guaranteed good, '包过的' = guaranteed pass."
        )
        usr = f"Chinese text: {text}"
        
        logger.info(f"DEBUG GPT judge_bao_de: input text='{text}'")
        logger.info(f"DEBUG GPT system prompt: {sys}")
        logger.info(f"DEBUG GPT user prompt: {usr}")
        
        try:
            if not self.openai_client:
                logger.info(f"DEBUG GPT: No OpenAI client, returning NOT_FOR_SURE")
                return "NOT_FOR_SURE"
                
            r = await self.openai_client.chat.completions.create(
                model="gpt-5-mini", 
                messages=[{"role":"system","content":sys},{"role":"user","content":usr}]
            )
            result = (r.choices[0].message.content or "").strip()
            logger.info(f"DEBUG GPT: Raw response='{result}'")
            logger.info(f"GPT bao_de judgment result: '{result}' for text: '{text}'")
            return result
        except Exception as e:
            logger.error(f"GPT bao_de judgment failed: {e}")
            return "NOT_FOR_SURE"