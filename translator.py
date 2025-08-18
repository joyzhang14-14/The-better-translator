import asyncio
import logging
import deepl
from preprocess import preprocess, preprocess_with_emoji_extraction, restore_emojis, FSURE_HEAD, FSURE_SEP, has_bao_de_pattern

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
        
        # Extract emojis from input text before any processing
        text_without_emojis, extracted_emojis = preprocess_with_emoji_extraction(text, direction, skip_bao_de=True)
        
        if direction == "zh_to_en":
            original_text = self._apply_dictionary(text_without_emojis, "zh_to_en", custom_map)
            gpt_processed = False
            # Debug logging
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"DEBUG translate_text: input='{text}', without_emojis='{text_without_emojis}', after_dict='{original_text}'")
            if has_bao_de_pattern(original_text):
                logger.info(f"DEBUG: Detected bao_de pattern in '{original_text}', calling GPT")
                gpt_result = await self.gpt_handler.judge_bao_de(original_text)
                logger.info(f"DEBUG: GPT result for '{original_text}': '{gpt_result}'")
                if gpt_result != "NOT_FOR_SURE":
                    logger.info(f"DEBUG: Returning GPT result: '{gpt_result}'")
                    # Restore emojis to GPT result
                    return restore_emojis(gpt_result, extracted_emojis)
                else:
                    logger.info(f"DEBUG: GPT said NOT_FOR_SURE, continuing with normal processing")
                    gpt_processed = True
            else:
                logger.info(f"DEBUG: No bao_de pattern detected in '{original_text}'")
            
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
                # Restore emojis to output
                return restore_emojis(out or "/", extracted_emojis)
            translated_result = await self._call_translate(pre, "Chinese", "English")
            # Restore emojis to translated result
            return restore_emojis(translated_result, extracted_emojis)
        else:
            pre = preprocess(self._apply_dictionary(text_without_emojis, "en_to_zh", custom_map), "en_to_zh")
            translated_result = await self._call_translate(pre, "English", "Chinese (Simplified)")
            # Restore emojis to translated result
            return restore_emojis(translated_result, extracted_emojis)

    async def _translate_with_context(self, text: str, direction: str, custom_map: dict, context: str) -> str:
        try:
            # Extract emojis from input text before processing
            text_without_emojis, extracted_emojis = preprocess_with_emoji_extraction(text, direction, skip_bao_de=True)
            # Also extract emojis from context
            context_without_emojis, context_emojis = preprocess_with_emoji_extraction(context, direction, skip_bao_de=True)
            
            if direction == "zh_to_en":
                text_dict_applied = self._apply_dictionary(text_without_emojis, "zh_to_en", custom_map)
                gpt_processed = False
                if has_bao_de_pattern(text_dict_applied):
                    gpt_result = await self.gpt_handler.judge_bao_de(text_dict_applied)
                    if gpt_result != "NOT_FOR_SURE":
                        # Restore emojis to GPT result
                        return restore_emojis(gpt_result, extracted_emojis)
                    else:
                        gpt_processed = True
                
                context_processed = preprocess(self._apply_dictionary(context_without_emojis, "zh_to_en", custom_map), "zh_to_en")
                text_processed = preprocess(text_dict_applied, "zh_to_en", skip_bao_de=gpt_processed)
                src_lang = "Chinese"
                tgt_lang = "English"
            else:
                context_processed = preprocess(self._apply_dictionary(context_without_emojis, "en_to_zh", custom_map), "en_to_zh")
                text_processed = preprocess(self._apply_dictionary(text_without_emojis, "en_to_zh", custom_map), "en_to_zh")
                src_lang = "English" 
                tgt_lang = "Chinese (Simplified)"
            
            combined_text = f"{context_processed}\n{text_processed}"
            translated_combined = await self._call_translate(combined_text, src_lang, tgt_lang)
            
            if translated_combined == "/":
                fallback_result = await self._call_translate(text_processed, src_lang, tgt_lang)
                return restore_emojis(fallback_result, extracted_emojis)
            
            lines = translated_combined.split('\n')
            if len(lines) >= 2:
                reply_lines = lines[1:]
                reply_translation = '\n'.join(reply_lines).strip()
                result = reply_translation if reply_translation else translated_combined
                return restore_emojis(result, extracted_emojis)
            else:
                return restore_emojis(translated_combined, extracted_emojis)
                
        except Exception as e:
            logger.error(f"Context translation failed: {e}")
            # Extract emojis for fallback processing
            text_without_emojis, extracted_emojis = preprocess_with_emoji_extraction(text, direction, skip_bao_de=True)
            
            if direction == "zh_to_en":
                text_dict_applied = self._apply_dictionary(text_without_emojis, "zh_to_en", custom_map)
                gpt_processed = False
                if has_bao_de_pattern(text_dict_applied):
                    gpt_result = await self.gpt_handler.judge_bao_de(text_dict_applied)
                    if gpt_result != "NOT_FOR_SURE":
                        return restore_emojis(gpt_result, extracted_emojis)
                    else:
                        gpt_processed = True
                pre = preprocess(text_dict_applied, "zh_to_en", skip_bao_de=gpt_processed)
                fallback_result = await self._call_translate(pre, "Chinese", "English")
                return restore_emojis(fallback_result, extracted_emojis)
            else:
                pre = preprocess(self._apply_dictionary(text_without_emojis, "en_to_zh", custom_map), "en_to_zh")
                fallback_result = await self._call_translate(pre, "English", "Chinese (Simplified)")
                return restore_emojis(fallback_result, extracted_emojis)