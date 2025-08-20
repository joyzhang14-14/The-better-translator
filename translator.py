import asyncio
import logging
import os
import json
import deepl
from preprocess import preprocess, preprocess_with_emoji_extraction, restore_emojis, FSURE_HEAD, FSURE_SEP, has_bao_de_pattern
from glossary_handler import glossary_handler

logger = logging.getLogger(__name__)

def _load_json_or(path: str, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read().strip()
            return json.loads(txt) if txt else fallback
    except Exception:
        return fallback

def _is_glossary_enabled(guild_id: str) -> bool:
    """Check if glossary detection is enabled for the guild (default: True)"""
    if not guild_id:
        return True  # Default to enabled
    
    CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
    config = _load_json_or(CONFIG_PATH, {})
    guild_config = config.get("guilds", {}).get(guild_id, {})
    return guild_config.get("glossary_enabled", True)  # Default: enabled

class Translator:
    def __init__(self, deepl_client, gpt_handler):
        self.deepl_client = deepl_client
        self.gpt_handler = gpt_handler

    async def _call_translate(self, src_text: str, src_lang: str, tgt_lang: str, fallback_to_simple: bool = False) -> str:
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
            
            logger.info(f"DEEPL_DEBUG: Calling DeepL API (fallback_mode: {fallback_to_simple})")
            logger.info(f"DEEPL_DEBUG: Input text: {repr(src_text)}")
            logger.info(f"DEEPL_DEBUG: Source lang: {source_lang}, Target lang: {target_lang}")
            
            result = await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: self.deepl_client.translate_text(src_text, target_lang=target_lang, source_lang=source_lang)
            )
            
            logger.info(f"DEEPL_DEBUG: Raw DeepL result: {repr(result.text)}")
            
            out = result.text.strip()
            
            logger.info(f"DEEPL_DEBUG: Final output: {repr(out)}")
            
            # Check if result is empty or just whitespace
            if not out or out.isspace():
                logger.warning(f"DEEPL_DEBUG: Empty or whitespace result detected: {repr(out)}")
                return "/"
            
            # Check for potential truncation and retry with sentence splitting if detected
            if not fallback_to_simple and self._detect_potential_truncation(src_text, out, src_lang):
                logger.warning(f"DEEPL_DEBUG: Detected potential truncation, trying sentence splitting")
                retry_result = await self._retry_with_sentence_splitting(src_text, source_lang, target_lang)
                if retry_result and retry_result != "/" and retry_result.strip():
                    logger.info(f"DEEPL_DEBUG: Sentence splitting result: {repr(retry_result)}")
                    return retry_result
                else:
                    logger.info(f"DEEPL_DEBUG: Sentence splitting failed, using original result")
            
            return out or "/"
        except Exception as e:
            logger.error(f"DeepL translation failed: {e}")
            return "/"

    async def _call_translate_simple(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        """Simple translation without context - used as fallback when context translation returns empty"""
        logger.info(f"FALLBACK_DEBUG: Calling simple translation without context")
        logger.info(f"FALLBACK_DEBUG: Input: {repr(src_text)}")
        
        return await self._call_translate(src_text, src_lang, tgt_lang, fallback_to_simple=True)

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

    async def translate_text(self, text: str, direction: str, custom_map: dict, context: str = None, history_messages: list = None, guild_id: str = None, user_name: str = "用户") -> str:
        # Traditional Chinese conversion now handled in preprocess functions
        
        # Priority: explicit reply context > message history context > normal translation
        if context:
            # Context will be processed by _translate_with_context which uses preprocess_with_emoji_extraction
            return await self._translate_with_context(text, direction, custom_map, context, guild_id)
        elif history_messages:
            # History messages will be processed by _translate_with_message_history which uses preprocess_with_emoji_extraction
            return await self._translate_with_message_history(text, direction, custom_map, history_messages, guild_id)
        
        # Extract emojis from input text before any processing
        text_without_emojis, extracted_emojis = preprocess_with_emoji_extraction(text, direction, skip_bao_de=True)
        
        # Apply dictionary first (legacy dictionary support)
        dict_applied_text = self._apply_dictionary(text_without_emojis, direction, custom_map)
        
        # Glossary processing (skip if disabled for this guild)
        if guild_id and _is_glossary_enabled(guild_id):
            # Determine source language for glossary matching
            if direction == "zh_to_en":
                source_lang = "中文"
            else:
                source_lang = "英文"
            
            logger.info(f"GLOSSARY_DEBUG: Processing glossary for guild {guild_id} (enabled)")
            # Apply mandatory glossary replacements first
            glossary_processed_text = glossary_handler.apply_mandatory_replacements(dict_applied_text, guild_id, source_lang)
            
            # Check for GPT-based glossary candidates
            gpt_candidates = glossary_handler.get_gpt_candidates(glossary_processed_text, guild_id, source_lang)
            
            if gpt_candidates:
                # Get context for GPT judgment (use history messages or empty list)
                context_for_gpt = history_messages if history_messages else []
                
                for source_term, entry in gpt_candidates:
                    should_replace = await self.gpt_handler.judge_glossary_replacement(
                        glossary_processed_text, entry, context_for_gpt, user_name
                    )
                    
                    if should_replace:
                        # Apply replacement
                        if entry["source_language"] == entry["target_language"]:
                            # Same language replacement
                            if source_lang == "英文":
                                import re
                                pattern_escaped = re.escape(source_term)
                                boundary_pattern = rf"(?<![A-Za-z0-9]){pattern_escaped}(?![A-Za-z0-9])"
                                glossary_processed_text = re.sub(boundary_pattern, entry["target_text"], glossary_processed_text, flags=re.IGNORECASE)
                            else:
                                glossary_processed_text = glossary_processed_text.replace(source_term, entry["target_text"])
                        else:
                            # Cross-language replacement - use placeholder
                            placeholder = f"GLOSSARYTERM{abs(hash(source_term))}"
                            if source_lang == "英文":
                                import re
                                pattern_escaped = re.escape(source_term)
                                boundary_pattern = rf"(?<![A-Za-z0-9]){pattern_escaped}(?![A-Za-z0-9])"
                                glossary_processed_text = re.sub(boundary_pattern, placeholder, glossary_processed_text, flags=re.IGNORECASE)
                            else:
                                glossary_processed_text = glossary_processed_text.replace(source_term, placeholder)
                            
                            # Store for post-translation replacement
                            if not hasattr(glossary_handler, '_pending_replacements'):
                                glossary_handler._pending_replacements = {}
                            session_key = "default"  # Use default session key for consistency
                            if session_key not in glossary_handler._pending_replacements:
                                glossary_handler._pending_replacements[session_key] = {}
                            glossary_handler._pending_replacements[session_key][placeholder] = entry["target_text"]
            
            processed_text = glossary_processed_text
        elif guild_id and not _is_glossary_enabled(guild_id):
            logger.info(f"GLOSSARY_DEBUG: Glossary processing disabled for guild {guild_id}, skipping")
            processed_text = dict_applied_text
        else:
            processed_text = dict_applied_text
        
        # Continue with existing bao_de logic for Chinese to English
        if direction == "zh_to_en":
            gpt_processed = False
            logger.info(f"DEBUG translate_text: input='{text}', processed='{processed_text}'")
            if has_bao_de_pattern(processed_text):
                logger.info(f"DEBUG: Detected bao_de pattern in '{processed_text}', calling GPT")
                gpt_result = await self.gpt_handler.judge_bao_de(processed_text)
                logger.info(f"DEBUG: GPT result for '{processed_text}': '{gpt_result}'")
                if gpt_result != "NOT_FOR_SURE":
                    logger.info(f"DEBUG: Returning GPT result: '{gpt_result}'")
                    # Apply cross-language glossary replacements to GPT result if needed
                    final_result = glossary_handler.restore_cross_language_replacements(gpt_result, "default")
                    return restore_emojis(final_result, extracted_emojis)
                else:
                    logger.info(f"DEBUG: GPT said NOT_FOR_SURE, continuing with normal processing")
                    gpt_processed = True
            
            pre = preprocess(processed_text, "zh_to_en", skip_bao_de=gpt_processed)
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
                # Apply cross-language glossary replacements if needed
                final_result = glossary_handler.restore_cross_language_replacements(out or "/", "default")
                return restore_emojis(final_result, extracted_emojis)
            
            translated_result = await self._call_translate(pre, "Chinese", "English")
            # Apply cross-language glossary replacements if needed
            final_result = glossary_handler.restore_cross_language_replacements(translated_result, "default")
            return restore_emojis(final_result, extracted_emojis)
        else:
            pre = preprocess(processed_text, "en_to_zh")
            translated_result = await self._call_translate(pre, "English", "Chinese (Simplified)")
            # Apply cross-language glossary replacements if needed
            final_result = glossary_handler.restore_cross_language_replacements(translated_result, "default")
            return restore_emojis(final_result, extracted_emojis)

    async def _translate_with_context(self, text: str, direction: str, custom_map: dict, context: str, guild_id: str = None) -> str:
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
            
            # Use context for better understanding but only translate the current input
            context_prompt = f"Please translate the following text considering the context provided. Context: {context_processed}\n\nText to translate: {text_processed}"
            translated_result = await self._call_translate(context_prompt, src_lang, tgt_lang)
            
            # Check if translation failed or returned empty
            if translated_result == "/" or not translated_result.strip():
                logger.warning(f"CONTEXT_DEBUG: Context-aware translation failed or empty, trying simple fallback")
                fallback_result = await self._call_translate_simple(text_processed, src_lang, tgt_lang)
                return restore_emojis(fallback_result, extracted_emojis)
                
            return restore_emojis(translated_result, extracted_emojis)
                
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

    async def _translate_with_message_history(self, text: str, direction: str, custom_map: dict, history_messages: list, guild_id: str = None) -> str:
        """Translate text with message history context for better fluency"""
        try:
            # Extract emojis from current text
            text_without_emojis, extracted_emojis = preprocess_with_emoji_extraction(text, direction, skip_bao_de=True)
            
            if direction == "zh_to_en":
                text_dict_applied = self._apply_dictionary(text_without_emojis, "zh_to_en", custom_map)
                gpt_processed = False
                if has_bao_de_pattern(text_dict_applied):
                    gpt_result = await self.gpt_handler.judge_bao_de(text_dict_applied)
                    if gpt_result != "NOT_FOR_SURE":
                        # GPT determined this is "for sure" meaning and provided translation
                        return restore_emojis(gpt_result, extracted_emojis)
                    else:
                        gpt_processed = True
                
                # Process history messages
                history_processed = []
                for hist_msg in history_messages:
                    hist_without_emojis, _ = preprocess_with_emoji_extraction(hist_msg, direction, skip_bao_de=True)
                    hist_dict_applied = self._apply_dictionary(hist_without_emojis, "zh_to_en", custom_map)
                    hist_processed_text = preprocess(hist_dict_applied, "zh_to_en")
                    history_processed.append(hist_processed_text)
                
                text_processed = preprocess(text_dict_applied, "zh_to_en", skip_bao_de=gpt_processed)
                src_lang = "Chinese"
                tgt_lang = "English"
            else:
                # Process history messages for en_to_zh
                history_processed = []
                for hist_msg in history_messages:
                    hist_without_emojis, _ = preprocess_with_emoji_extraction(hist_msg, direction, skip_bao_de=True)
                    hist_dict_applied = self._apply_dictionary(hist_without_emojis, "en_to_zh", custom_map)
                    hist_processed_text = preprocess(hist_dict_applied, "en_to_zh")
                    history_processed.append(hist_processed_text)
                    
                text_processed = preprocess(self._apply_dictionary(text_without_emojis, "en_to_zh", custom_map), "en_to_zh")
                src_lang = "English" 
                tgt_lang = "Chinese (Simplified)"
            
            # Combine history and current message
            all_messages = history_processed + [text_processed]
            combined_text = "\n".join(all_messages)
            
            # Translate the combined text
            translated_combined = await self._call_translate(combined_text, src_lang, tgt_lang)
            
            # Check if translation failed or returned empty
            if translated_combined == "/" or not translated_combined.strip():
                logger.warning(f"HISTORY_DEBUG: Combined translation failed or empty, trying simple fallback")
                fallback_result = await self._call_translate_simple(text_processed, src_lang, tgt_lang)
                return restore_emojis(fallback_result, extracted_emojis)
            
            # Extract the current message translation (last line)
            lines = translated_combined.split('\n')
            if len(lines) >= len(all_messages):
                # Get the line corresponding to the current message (last line)
                current_message_translation = lines[-1].strip()
                
                # Check if extracted result is empty or just whitespace
                if not current_message_translation or current_message_translation.isspace():
                    logger.warning(f"HISTORY_DEBUG: Extracted current message translation is empty, trying simple fallback")
                    fallback_result = await self._call_translate_simple(text_processed, src_lang, tgt_lang)
                    return restore_emojis(fallback_result, extracted_emojis)
                
                result = current_message_translation
                return restore_emojis(result, extracted_emojis)
            else:
                # If splitting failed, check if whole result is meaningful
                if not translated_combined.strip() or translated_combined.strip() == "/":
                    logger.warning(f"HISTORY_DEBUG: Whole result is empty, trying simple fallback")
                    fallback_result = await self._call_translate_simple(text_processed, src_lang, tgt_lang)
                    return restore_emojis(fallback_result, extracted_emojis)
                
                return restore_emojis(translated_combined, extracted_emojis)
                
        except Exception as e:
            logger.error(f"Message history translation failed: {e}")
            # Fallback to normal translation
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
    def _detect_potential_truncation(self, input_text: str, output_text: str, src_lang: str) -> bool:
        """Detect if DeepL might have truncated the translation"""
        # Skip detection for very short texts
        if len(input_text) < 30:
            return False
            
        # For English input, check for common truncation patterns
        if src_lang == "English":
            # Count question marks
            input_questions = input_text.count('?')
            output_questions = output_text.count('？')
            
            # If input has 2+ questions but output has fewer, likely truncated
            if input_questions >= 2 and output_questions < input_questions:
                logger.info(f"DEEPL_DEBUG: Question count mismatch: input={input_questions}, output={output_questions}")
                return True
                
            # If input ends with question but output doesn't
            if input_text.strip().endswith('?') and not output_text.strip().endswith('？'):
                logger.info(f"DEEPL_DEBUG: Input ends with ? but output doesn't end with ？")
                return True
                
            # Check for specific patterns that indicate truncation
            # If output is significantly shorter than expected for English->Chinese
            expected_min_length = len(input_text) * 0.4  # Very conservative estimate
            if len(output_text) < expected_min_length:
                logger.info(f"DEEPL_DEBUG: Output too short: {len(output_text)} < {expected_min_length}")
                return True
                
        return False

    async def _retry_with_sentence_splitting(self, src_text: str, source_lang: str, target_lang: str) -> str:
        """Retry translation using sentence splitting when truncation is detected"""
        try:
            # Split on sentence boundaries for English
            import re
            if source_lang == "EN":
                # More sophisticated splitting for English
                # Split on sentence endings but preserve them
                sentences = re.split(r'([.!?]+)', src_text.strip())
                
                # Reconstruct sentences with their punctuation
                reconstructed = []
                for i in range(0, len(sentences)-1, 2):
                    sentence = sentences[i].strip()
                    punct = sentences[i+1] if i+1 < len(sentences) else ""
                    if sentence:
                        reconstructed.append(sentence + punct)
                
                # Handle case where text doesn't end with punctuation
                if len(sentences) % 2 == 1 and sentences[-1].strip():
                    reconstructed.append(sentences[-1].strip())
                
                logger.info(f"DEEPL_DEBUG: Split into {len(reconstructed)} sentences: {reconstructed}")
                
                if len(reconstructed) <= 1:
                    return None  # No splitting possible
                
                # Translate each sentence separately
                translations = []
                for sentence in reconstructed:
                    if sentence.strip():
                        result = await asyncio.get_event_loop().run_in_executor(
                            None, 
                            lambda s=sentence: self.deepl_client.translate_text(s, target_lang=target_lang, source_lang=source_lang)
                        )
                        translations.append(result.text.strip())
                        logger.info(f"DEEPL_DEBUG: '{sentence}' -> '{result.text.strip()}'")
                
                # Combine translations with spaces
                combined = " ".join(translations)
                logger.info(f"DEEPL_DEBUG: Combined sentence translations: '{combined}'")
                return combined
                
        except Exception as e:
            logger.error(f"Sentence splitting retry failed: {e}")
            
        return None
