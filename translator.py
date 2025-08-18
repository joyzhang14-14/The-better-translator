import asyncio
import logging
import deepl
from preprocess import preprocess, FSURE_HEAD, FSURE_SEP, has_bao_de_pattern

logger = logging.getLogger(__name__)

class Translator:
    def __init__(self, deepl_client, gpt_handler):
        self.deepl_client = deepl_client
        self.gpt_handler = gpt_handler

    async def _call_translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        if not src_text:
            return "/"
        
        try:
            if src_lang == "Chinese":
                source_lang = "ZH"
            elif src_lang == "English":
                source_lang = "EN"
            else:
                source_lang = None
            
            if tgt_lang.startswith("Chinese"):
                target_lang = "ZH"
            elif tgt_lang == "English":
                target_lang = "EN-US"
            else:
                logger.error(f"Unsupported target language: {tgt_lang}")
                return "/"
            
            result = await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: self.deepl_client.translate_text(src_text, target_lang=target_lang, source_lang=source_lang)
            )
            out = result.text.strip()
            return out or "/"
        except Exception as e:
            logger.error(f"DeepL translation failed: {e}")
            return "/"

    def _apply_dictionary(self, text: str, direction: str, custom_map: dict) -> str:
        s = text or ""
        if not custom_map:
            return s
        if direction == "zh_to_en":
            for zh, en in sorted(custom_map.items(), key=lambda kv: len(kv[0]), reverse=True):
                s = s.replace(zh, en)
        else:
            inv = {v: k for k, v in custom_map.items()}
            for en, zh in sorted(inv.items(), key=lambda kv: len(kv[0]), reverse=True):
                import re
                pat = re.compile(rf"\b{re.escape(en)}\b", re.IGNORECASE)
                s = pat.sub(zh, s)
        return s

    async def _preprocess_with_gpt_check(self, text: str, direction: str, custom_map: dict = None) -> str:
        if direction == "zh_to_en" and custom_map:
            text_dict_applied = self._apply_dictionary(text, "zh_to_en", custom_map)
            skip_bao_de = has_bao_de_pattern(text_dict_applied)
            return preprocess(text_dict_applied, direction, skip_bao_de=skip_bao_de)
        else:
            processed_text = self._apply_dictionary(text, direction, custom_map) if custom_map else text
            return preprocess(processed_text, direction)

    async def translate_text(self, text: str, direction: str, custom_map: dict, context: str = None) -> str:
        if context:
            return await self._translate_with_context(text, direction, custom_map, context)
        
        if direction == "zh_to_en":
            original_text = self._apply_dictionary(text, "zh_to_en", custom_map)
            gpt_processed = False
            if has_bao_de_pattern(original_text):
                gpt_result = await self.gpt_handler.judge_bao_de(original_text)
                if gpt_result != "NOT_FOR_SURE":
                    return gpt_result
                else:
                    gpt_processed = True
            
            pre = preprocess(original_text, "zh_to_en", skip_bao_de=gpt_processed)
            if pre.startswith(FSURE_HEAD):
                payload = pre[len(FSURE_HEAD):]
                if FSURE_SEP in payload:
                    core, tail = payload.split(FSURE_SEP, 1)
                else:
                    core, tail = payload, ""
                en_core = await self._call_translate(core, "Chinese", "English")
                en_tail = await self._call_translate(tail, "Chinese", "English") if tail.strip() else ""
                out = (en_core or "/")
                if out != "/":
                    out = out.strip().rstrip(".") + " for sure"
                    if en_tail and en_tail != "/":
                        out = out + ", " + en_tail
                return out or "/"
            return await self._call_translate(pre, "Chinese", "English")
        else:
            pre = preprocess(self._apply_dictionary(text, "en_to_zh", custom_map), "en_to_zh")
            return await self._call_translate(pre, "English", "Chinese (Simplified)")

    async def _translate_with_context(self, text: str, direction: str, custom_map: dict, context: str) -> str:
        try:
            if direction == "zh_to_en":
                text_dict_applied = self._apply_dictionary(text, "zh_to_en", custom_map)
                gpt_processed = False
                if has_bao_de_pattern(text_dict_applied):
                    gpt_result = await self.gpt_handler.judge_bao_de(text_dict_applied)
                    if gpt_result != "NOT_FOR_SURE":
                        return gpt_result
                    else:
                        gpt_processed = True
                
                context_processed = preprocess(self._apply_dictionary(context, "zh_to_en", custom_map), "zh_to_en")
                text_processed = preprocess(text_dict_applied, "zh_to_en", skip_bao_de=gpt_processed)
                src_lang = "Chinese"
                tgt_lang = "English"
            else:
                context_processed = preprocess(self._apply_dictionary(context, "en_to_zh", custom_map), "en_to_zh")
                text_processed = preprocess(self._apply_dictionary(text, "en_to_zh", custom_map), "en_to_zh")
                src_lang = "English" 
                tgt_lang = "Chinese (Simplified)"
            
            combined_text = f"{context_processed}\n{text_processed}"
            translated_combined = await self._call_translate(combined_text, src_lang, tgt_lang)
            
            if translated_combined == "/":
                return await self._call_translate(text_processed, src_lang, tgt_lang)
            
            lines = translated_combined.split('\n')
            if len(lines) >= 2:
                reply_lines = lines[1:]
                reply_translation = '\n'.join(reply_lines).strip()
                return reply_translation if reply_translation else translated_combined
            else:
                return translated_combined
                
        except Exception as e:
            logger.error(f"Context translation failed: {e}")
            if direction == "zh_to_en":
                text_dict_applied = self._apply_dictionary(text, "zh_to_en", custom_map)
                gpt_processed = False
                if has_bao_de_pattern(text_dict_applied):
                    gpt_result = await self.gpt_handler.judge_bao_de(text_dict_applied)
                    if gpt_result != "NOT_FOR_SURE":
                        return gpt_result
                    else:
                        gpt_processed = True
                pre = preprocess(text_dict_applied, "zh_to_en", skip_bao_de=gpt_processed)
                return await self._call_translate(pre, "Chinese", "English")
            else:
                pre = preprocess(self._apply_dictionary(text, "en_to_zh", custom_map), "en_to_zh")
                return await self._call_translate(pre, "English", "Chinese (Simplified)")