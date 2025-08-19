import logging
import re
from typing import List, Dict, Tuple

# Install opencc-python-reimplemented if not already installed
try:
    from opencc import OpenCC
    cc = OpenCC('t2s')  # Traditional to Simplified Chinese converter
except ImportError:
    # Fallback: basic manual mapping for common traditional characters
    cc = None

logger = logging.getLogger(__name__)

CUSTOM_EMOJI_RE = re.compile(r"<a?:\w{2,}>:\d+>")
UNICODE_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U00002600-\U000026FF\U0001F1E6-\U0001F1FF]+")

class GPTHandler:
    def __init__(self, openai_client):
        self.openai_client = openai_client
    
    def convert_traditional_to_simplified(self, text: str) -> str:
        """Convert traditional Chinese to simplified Chinese"""
        if not text:
            return text
        
        if cc:
            try:
                converted = cc.convert(text)
                if converted != text:
                    logger.info(f"Converted traditional to simplified: '{text}' → '{converted}'")
                return converted
            except Exception as e:
                logger.warning(f"OpenCC conversion failed: {e}, using fallback")
        
        # Fallback: basic manual mapping for common traditional characters
        traditional_to_simplified = {
            '繁體': '繁体', '體': '体', '國': '国', '語': '语', '來': '来', '過': '过',
            '時': '时', '會': '会', '個': '个', '們': '们', '學': '学', '說': '说',
            '話': '话', '長': '长', '開': '开', '關': '关', '經': '经', '對': '对',
            '現': '现', '發': '发', '這': '这', '樣': '样', '還': '还', '應': '应',
            '當': '当', '從': '从', '後': '后', '處': '处', '見': '见', '間': '间',
            '問': '问', '題': '题', '實': '实', '點': '点', '條': '条', '機': '机',
            '電': '电', '動': '动', '業': '业', '員': '员', '無': '无', '種': '种',
            '準': '准', '決': '决', '認': '认', '識': '识', '進': '进', '選': '选',
            '擇': '择', '變': '变', '華': '华', '質': '质', '級': '级', '類': '类'
        }
        
        result = text
        for trad, simp in traditional_to_simplified.items():
            if trad in result:
                result = result.replace(trad, simp)
        
        if result != text:
            logger.info(f"Fallback conversion: '{text}' → '{result}'")
        
        return result

    async def detect_language(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return "meaningless"
        
        # Step 1: Convert traditional Chinese to simplified Chinese
        t = self.convert_traditional_to_simplified(t)
        
        # Step 2: Remove emojis and clean text
        t2 = CUSTOM_EMOJI_RE.sub("", t)
        t2 = UNICODE_EMOJI_RE.sub("", t2)
        t2 = re.sub(r"(e?m+)+", "em", t2, flags=re.IGNORECASE)
        
        # Step 3: Count Chinese and English characters
        zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
        en_count = len(re.findall(r"[A-Za-z]", t2))
        
        # Step 4: Simple rule - any Chinese characters = Chinese
        if zh_count > 0:
            logger.info(f"Chinese characters detected ({zh_count} Chinese, {en_count} English), treating as Chinese")
            return "Chinese"
        elif en_count > 0:
            logger.info(f"Only English characters detected ({en_count} English), treating as English")
            return "English"
        else:
            return "meaningless"

    async def _ai_detect_language(self, text: str) -> str:
        """Legacy method - now just calls the main detect_language method"""
        # Convert traditional to simplified first
        text = self.convert_traditional_to_simplified(text)
        
        # Use the same simple logic as detect_language
        t2 = CUSTOM_EMOJI_RE.sub("", text)
        t2 = UNICODE_EMOJI_RE.sub("", t2)
        zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
        en_count = len(re.findall(r"[A-Za-z]", t2))
        
        if zh_count > 0:
            return "Chinese"
        elif en_count > 0:
            return "English"
        else:
            return "meaningless"

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

    async def judge_glossary_replacement(self, source_text: str, entry: Dict, context_messages: List[str], current_user: str) -> bool:
        """
        Judge whether a glossary entry should be replaced based on context
        Returns True if replacement should happen, False otherwise
        """
        source_lang = entry["source_language"]
        target_lang = entry["target_language"]
        source_term = entry["source_text"]
        target_term = entry["target_text"]
        
        # Build context string
        context_lines = []
        for i, msg in enumerate(context_messages, 1):
            context_lines.append(f"用户{i}: {msg}")
        
        current_line = f"用户{len(context_messages) + 1}: {source_text}"
        context_lines.append(current_line)
        
        context_text = "\n".join(context_lines)
        
        # Create different prompts based on language direction
        if source_lang == "中文" and target_lang == "英文":
            # Chinese to English
            sys_prompt = (
                f"分析对话内容和语境，判断用户最后一句话中的「{source_term}」是否应该被替换为英文「{target_term}」。"
                f"请根据对话的逻辑、语境和用户的意图来判断。"
                f"只回答「需要替换」或「不需要替换」，不要其他解释。"
            )
        elif source_lang == "英文" and target_lang == "中文":
            # English to Chinese  
            sys_prompt = (
                f"Analyze the conversation content and context to determine if the '{source_term}' in the user's last message should be replaced with Chinese '{target_term}'. "
                f"Please judge based on the conversation logic, context, and user's intent. "
                f"Only answer '需要替换' or '不需要替换', no other explanation."
            )
        elif source_lang == "中文" and target_lang == "中文":
            # Chinese to Chinese
            sys_prompt = (
                f"分析对话内容和语境，判断用户最后一句话中的「{source_term}」是否应该被替换为「{target_term}」。"
                f"请根据对话的逻辑、语境和用户的意图来判断这种替换是否合适。"
                f"只回答「需要替换」或「不需要替换」，不要其他解释。"
            )
        else:
            # English to English
            sys_prompt = (
                f"Analyze the conversation content and context to determine if the '{source_term}' in the user's last message should be replaced with '{target_term}'. "
                f"Please judge based on the conversation logic, context, and user's intent. "
                f"Only answer '需要替换' or '不需要替换', no other explanation."
            )
        
        usr_prompt = f"这是一份对话内容，你将会根据对话的逻辑和内容判断：\n{context_text}\n\n你觉得对话中{current_user}说的「{source_term}」需不需要被替换成（{target_lang}）「{target_term}」？"
        
        try:
            if not self.openai_client:
                logger.info("No OpenAI client available, defaulting to no replacement")
                return False
            
            logger.info(f"GPT glossary judgment for '{source_term}' -> '{target_term}'")
            r = await self.openai_client.chat.completions.create(
                model="gpt-5-mini",
                messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": usr_prompt}]
            )
            result = (r.choices[0].message.content or "").strip()
            logger.info(f"GPT glossary judgment result: '{result}'")
            
            # Check if GPT says to replace
            should_replace = "需要替换" in result
            return should_replace
            
        except Exception as e:
            logger.error(f"GPT glossary judgment failed: {e}")
            return False  # Default to no replacement on error