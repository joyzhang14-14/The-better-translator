import json
import re
import logging
from typing import Dict, List, Optional, Tuple
from storage import storage

logger = logging.getLogger(__name__)

GLOSSARIES_PATH = "glossaries.json"

def _load_json_or(path: str, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read().strip()
            return json.loads(txt) if txt else fallback
    except Exception:
        return fallback

class GlossaryHandler:
    def __init__(self):
        self.glossaries: Dict[str, Dict[str, Dict]] = {}
        self.load_glossaries()
    
    def load_glossaries(self):
        """Load glossaries from local file"""
        try:
            self.glossaries = _load_json_or(GLOSSARIES_PATH, {})
            logger.info(f"Loaded glossaries for {len(self.glossaries)} guilds")
        except Exception as e:
            logger.error(f"Failed to load glossaries: {e}")
            self.glossaries = {}
    
    async def load_from_cloud(self):
        """Load glossaries from cloud storage"""
        try:
            cloud_glossaries = await storage.load_json("glossaries", {})
            self.glossaries.update(cloud_glossaries)
            logger.info(f"Updated glossaries from cloud: {len(self.glossaries)} guilds")
        except Exception as e:
            logger.error(f"Failed to load glossaries from cloud: {e}")
    
    def find_glossary_matches(self, text: str, guild_id: str, source_language: str) -> List[Tuple[str, Dict]]:
        """Find all glossary entries that match the given text and language"""
        if guild_id not in self.glossaries:
            return []
        
        matches = []
        guild_glossaries = self.glossaries[guild_id]
        
        for entry_id, entry in guild_glossaries.items():
            if entry["source_language"] != source_language:
                continue
            
            source_text = entry["source_text"]
            
            # Check if the source text exists in the input text
            if self._text_matches(text, source_text, source_language):
                matches.append((source_text, entry))
        
        # Sort by source text length (longest first) to handle overlapping matches
        matches.sort(key=lambda x: len(x[0]), reverse=True)
        return matches
    
    def _text_matches(self, text: str, pattern: str, language: str) -> bool:
        """Check if pattern exists in text with proper word boundaries for English"""
        if language == "英文":
            # For English, check word boundaries to avoid partial matches
            # e.g., "ik" should not match "like" 
            pattern_escaped = re.escape(pattern)
            # Use word boundaries (\b) but also check for space/punctuation boundaries
            boundary_pattern = rf"(?<![A-Za-z0-9]){pattern_escaped}(?![A-Za-z0-9])"
            return bool(re.search(boundary_pattern, text, re.IGNORECASE))
        else:
            # For Chinese, simple substring match is sufficient
            return pattern in text
    
    def apply_mandatory_replacements(self, text: str, guild_id: str, source_language: str) -> str:
        """Apply all mandatory (non-GPT) replacements to the text"""
        matches = self.find_glossary_matches(text, guild_id, source_language)
        result = text
        
        for source_text, entry in matches:
            if not entry["needs_gpt"]:  # Mandatory replacement
                # Check if same language replacement
                if entry["source_language"] == entry["target_language"]:
                    # Direct replacement
                    if source_language == "英文":
                        # Use word boundary replacement for English
                        pattern_escaped = re.escape(source_text)
                        boundary_pattern = rf"(?<![A-Za-z0-9]){pattern_escaped}(?![A-Za-z0-9])"
                        result = re.sub(boundary_pattern, entry["target_text"], result, flags=re.IGNORECASE)
                    else:
                        # Simple replacement for Chinese
                        result = result.replace(source_text, entry["target_text"])
                else:
                    # Cross-language replacement - use placeholder
                    placeholder = f"__GLOSSARY_{len(source_text)}_{hash(source_text)}__"
                    if source_language == "英文":
                        pattern_escaped = re.escape(source_text)
                        boundary_pattern = rf"(?<![A-Za-z0-9]){pattern_escaped}(?![A-Za-z0-9])"
                        result = re.sub(boundary_pattern, placeholder, result, flags=re.IGNORECASE)
                    else:
                        result = result.replace(source_text, placeholder)
                    
                    # Store the replacement for post-translation processing
                    if not hasattr(self, '_pending_replacements'):
                        self._pending_replacements = {}
                    self._pending_replacements[placeholder] = entry["target_text"]
        
        return result
    
    def restore_cross_language_replacements(self, translated_text: str) -> str:
        """Restore cross-language replacements after translation"""
        if not hasattr(self, '_pending_replacements'):
            return translated_text
        
        result = translated_text
        for placeholder, replacement in self._pending_replacements.items():
            result = result.replace(placeholder, replacement)
        
        # Clear pending replacements
        self._pending_replacements = {}
        return result
    
    def get_gpt_candidates(self, text: str, guild_id: str, source_language: str) -> List[Tuple[str, Dict]]:
        """Get glossary entries that need GPT judgment"""
        matches = self.find_glossary_matches(text, guild_id, source_language)
        return [(source_text, entry) for source_text, entry in matches if entry["needs_gpt"]]

# Global instance
glossary_handler = GlossaryHandler()