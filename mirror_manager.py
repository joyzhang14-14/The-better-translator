import json
import os
import logging
from collections import deque
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class MirrorManager:
    def __init__(self, mirror_path: str, max_per_guild: int = 4000):
        self.mirror_path = mirror_path
        self.max_per_guild = max_per_guild
        self.mirror_map: Dict[int, Dict[int, Dict[int, int]]] = {}

    def _coerce_int_keys(self, obj):
        if isinstance(obj, dict):
            new = {}
            for k, v in obj.items():
                try:
                    ik = int(k)
                except (ValueError, TypeError):
                    ik = k
                new[ik] = self._coerce_int_keys(v)
            return new
        if isinstance(obj, list):
            return [self._coerce_int_keys(x) for x in obj]
        return obj

    def load(self):
        try:
            if os.path.exists(self.mirror_path):
                with open(self.mirror_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.mirror_map = self._coerce_int_keys(data) or {}
                logger.info("Loaded mirror map from %s (%d guilds)", self.mirror_path, len(self.mirror_map))
        except Exception as e:
            logger.exception("Load mirror_map failed: %s", e)
            self.mirror_map = {}

    def save(self):
        try:
            with open(self.mirror_path, "w", encoding="utf-8") as f:
                json.dump(self.mirror_map, f, ensure_ascii=False, separators=(",", ":"))
        except Exception as e:
            logger.exception("Save mirror_map failed: %s", e)

    def _prune(self, gid: int):
        if self.max_per_guild <= 0:
            return
        g = self.mirror_map.setdefault(gid, {})
        over = max(0, len(g) - self.max_per_guild)
        if over <= 0:
            return
        for _ in range(over):
            try:
                k = next(iter(g))
            except StopIteration:
                break
            g.pop(k, None)

    def add(self, gid: int, src_id: int, ch_id: int, mapped_id: int):
        self.mirror_map.setdefault(gid, {}).setdefault(src_id, {})[ch_id] = mapped_id
        self._prune(gid)
        self.save()

    def get_neighbors(self, gid: int, src_id: int) -> Dict[int, int]:
        neighbors = self.mirror_map.get(gid, {}).get(src_id, {})
        return neighbors

    def find_mirror_id(self, gid: int, src_msg_id: int, target_channel_id: int) -> Optional[int]:
        if gid not in self.mirror_map or src_msg_id not in self.mirror_map[gid]:
            return None
        visited = set([src_msg_id])
        q = deque([src_msg_id])
        while q:
            cur = q.popleft()
            neighbors: Dict[int, int] = self.mirror_map[gid].get(cur, {})
            if target_channel_id in neighbors:
                return neighbors[target_channel_id]
            for nxt in neighbors.values():
                if nxt not in visited:
                    visited.add(nxt)
                    q.append(nxt)
        return None