"""
Persistent storage for dictionary and other data
Supports file-based storage (local) and URL-based storage (cloud)
"""

import json
import os
import aiohttp
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class PersistentStorage:
    def __init__(self):
        self.storage_type = os.environ.get('STORAGE_TYPE', 'file')  # 'file' or 'url'
        self.storage_url = os.environ.get('STORAGE_URL', '')  # For URL-based storage
        self.storage_token = os.environ.get('STORAGE_TOKEN', '')  # Authentication token
        self.bin_id = os.environ.get('JSONBIN_ID', '689c188a43b1c97be91d1685')  # JSONBin ID
        
    async def load_json(self, key: str, fallback: Dict[str, Any] = None) -> Dict[str, Any]:
        """Load JSON data from persistent storage"""
        if fallback is None:
            fallback = {}
            
        if self.storage_type == 'url' and self.storage_url:
            return await self._load_from_url(key, fallback)
        else:
            return await self._load_from_file(key, fallback)
    
    async def save_json(self, key: str, data: Dict[str, Any]) -> bool:
        """Save JSON data to persistent storage"""
        if self.storage_type == 'url' and self.storage_url:
            return await self._save_to_url(key, data)
        else:
            return await self._save_to_file(key, data)
    
    async def _load_from_file(self, key: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
        """Load from local file"""
        try:
            file_path = f"{key}.json"
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return fallback
        except Exception as e:
            logger.error(f"Failed to load {key} from file: {e}")
            return fallback
    
    async def _save_to_file(self, key: str, data: Dict[str, Any]) -> bool:
        """Save to local file"""
        try:
            file_path = f"{key}.json"
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"Failed to save {key} to file: {e}")
            return False
    
    async def _load_from_url(self, key: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
        """Load from URL-based storage (JSONBin)"""
        try:
            # Load all data from the bin and extract the specific key
            all_data = await self._load_existing_bin()
            
            if key in all_data:
                logger.info(f"Loaded {key} from JSONBin successfully")
                return all_data[key]
            else:
                logger.info(f"Storage key {key} not found in JSONBin, using fallback")
                return fallback
        except Exception as e:
            logger.error(f"Failed to load {key} from URL: {e}")
            return fallback
    
    async def _save_to_url(self, key: str, data: Dict[str, Any]) -> bool:
        """Save to URL-based storage (JSONBin)"""
        try:
            # First try to load existing data from the bin
            existing_data = await self._load_existing_bin()
            
            # Update the specific key in the existing data
            existing_data[key] = data
            
            # Try to update existing bin first
            url = f"{self.storage_url}/{self.bin_id}"
            headers = {
                'Content-Type': 'application/json',
                'X-Master-Key': self.storage_token
            }
            
            logger.info(f"Attempting to save {key} to JSONBin at {url}")
            logger.info(f"Using master key: {self.storage_token[:10]}...")
            
            async with aiohttp.ClientSession() as session:
                # Use PUT to update existing bin
                async with session.put(url, json=existing_data, headers=headers) as response:
                    response_text = await response.text()
                    logger.info(f"JSONBin response: HTTP {response.status} - {response_text[:200]}")
                    
                    if response.status in [200, 201]:
                        logger.info(f"Successfully saved {key} to JSONBin")
                        return True
                    elif response.status == 404:
                        # Bin doesn't exist, create it
                        logger.info("Bin not found, creating new bin...")
                        return await self._create_new_bin(existing_data)
                    else:
                        logger.error(f"Failed to save {key}: HTTP {response.status} - {response_text}")
                        return False
        except Exception as e:
            logger.error(f"Failed to save {key} to URL: {e}")
            return False

    async def _create_new_bin(self, data: Dict[str, Any]) -> bool:
        """Create a new JSONBin"""
        try:
            url = f"{self.storage_url}"
            headers = {
                'Content-Type': 'application/json',
                'X-Master-Key': self.storage_token,
                'X-Bin-Name': 'discord-bot-storage',
                'X-Bin-Private': 'false'
            }
            
            logger.info(f"Creating new JSONBin at {url}")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, headers=headers) as response:
                    response_text = await response.text()
                    logger.info(f"Create bin response: HTTP {response.status} - {response_text[:200]}")
                    
                    if response.status in [200, 201]:
                        # Extract the new bin ID from response
                        try:
                            response_data = await response.json()
                            new_bin_id = response_data.get('metadata', {}).get('id')
                            if new_bin_id:
                                logger.info(f"Successfully created new bin with ID: {new_bin_id}")
                                logger.info(f"Update your JSONBIN_ID environment variable to: {new_bin_id}")
                            return True
                        except:
                            logger.info("Successfully created new bin but couldn't extract ID")
                            return True
                    else:
                        logger.error(f"Failed to create bin: HTTP {response.status} - {response_text}")
                        return False
        except Exception as e:
            logger.error(f"Failed to create new bin: {e}")
            return False

    async def _load_existing_bin(self) -> Dict[str, Any]:
        """Load all existing data from the JSONBin"""
        try:
            url = f"{self.storage_url}/{self.bin_id}/latest"
            headers = {
                'X-Master-Key': self.storage_token
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        response_data = await response.json()
                        return response_data.get('record', response_data)
                    else:
                        return {}
        except Exception:
            return {}

# Global storage instance
storage = PersistentStorage()