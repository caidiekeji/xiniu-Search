"""
xiniubot SimHash 内容近似去重
==============================
对一篇文档的 token 计算 64 位 SimHash 指纹,
两个指纹的海明距离 <= 阈值时视为内容近似重复.

用于: 同一篇正文出现在不同 URL / 镜像站 / 拼接页时避免重复入库.
"""

import hashlib

_BITS = 64
_MASK = (1 << _BITS) - 1


def _token_hash(token: str) -> int:
    """稳定 64 位哈希 (跨进程一致, 不依赖内置 hash 随机化)."""
    digest = hashlib.md5(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & _MASK


def simhash(tokens) -> int:
    """根据 token 列表计算 64 位 SimHash 指纹."""
    v = [0] * _BITS
    for token in set(tokens):
        if not token:
            continue
        h = _token_hash(token)
        for i in range(_BITS):
            if h & (1 << i):
                v[i] += 1
            else:
                v[i] -= 1
    fingerprint = 0
    for i in range(_BITS):
        if v[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint


def hamming(a: int, b: int) -> int:
    """两个指纹的海明距离."""
    return bin((a ^ b) & _MASK).count("1")
