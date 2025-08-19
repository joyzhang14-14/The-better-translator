#!/usr/bin/env python3
import re
import sys
sys.path.append('.')

from preprocess import preprocess, extract_emojis
from gpt_handler import GPTHandler
import asyncio

# Mock openai client
class MockOpenAI:
    pass

async def test_language_detection():
    # Create GPT handler
    gpt_handler = GPTHandler(MockOpenAI())
    
    # Test cases from user
    test_cases = [
        "比较级和最高级",  # Should be Chinese
        "better比较好",     # Should be Mixed  
        "best最好"         # Should be Mixed
    ]
    
    print("=== Testing Language Detection Logic ===")
    sys.stdout.reconfigure(encoding='utf-8')
    
    for text in test_cases:
        print(f"\nTesting: {repr(text)}")
        
        # Step 1: Preprocess (like in bot.py line 1051)
        raw = preprocess(text, "zh_to_en", skip_bao_de=True)
        print(f"After preprocess: {repr(raw)}")
        
        # Step 2: strip_banner (like in bot.py line 1078)
        from bot import strip_banner
        txt = strip_banner(raw)
        print(f"After strip_banner: {repr(txt)}")
        
        # Step 3: Traditional Chinese conversion (like in detect_language)
        t = gpt_handler.convert_traditional_to_simplified(txt)
        print(f"After traditional conversion: {repr(t)}")
        
        # Step 4: Extract emojis
        text_without_emojis, _ = extract_emojis(t)
        print(f"After emoji extraction: {repr(text_without_emojis)}")
        
        # Step 5: Character counting
        t2 = text_without_emojis
        t2 = re.sub(r"(e?m+)+", "em", t2, flags=re.IGNORECASE)
        zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
        en_count = len(re.findall(r"[A-Za-z]", t2))
        
        print(f"Character counts: Chinese={zh_count}, English={en_count}")
        
        # Step 6: Language decision
        if zh_count > 0 and en_count > 0:
            result = "Mixed"
        elif zh_count > 0:
            result = "Chinese"
        elif en_count > 0:
            result = "English"
        else:
            result = "meaningless"
            
        print(f"Final result: {result}")
        print("-" * 50)

if __name__ == "__main__":
    asyncio.run(test_language_detection())