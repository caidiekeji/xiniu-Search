"""
xiniubot 中文分词器
===================
核心算法:
  1. 基于 Trie 词典构建 DAG (有向无环图)
  2. 动态规划求最大概率路径
  3. HMM Viterbi 解码未登录词
  4. 英文 / 数字 / 中文混合切分

词典格式 (每行):  词语 频率(整数,越大越常见) [词性]
"""

import math
import os
import re
from collections import defaultdict

import config

# ═══════════════════════════════════════════════════════
#  Trie 节点
# ═══════════════════════════════════════════════════════

class TrieNode:
    __slots__ = ("children", "freq", "is_word")

    def __init__(self):
        self.children: dict[str, "TrieNode"] = {}
        self.freq: int = 0
        self.is_word: bool = False


# ═══════════════════════════════════════════════════════
#  HMM 模型参数  (BMES 四状态)
# ═══════════════════════════════════════════════════════

# 状态: B=0 词首, M=1 词中, E=2 词尾, S=3 单字成词
B, M, E, S = 0, 1, 2, 3
NUM_STATES = 4

# 初始概率 (log)
HMM_START = {
    B: -0.2668,   # 大部分句子以词首开始
    M: -1e9,      # 几乎不可能
    E: -1e9,
    S: -0.7165,
}

# 转移概率 (log)  trans[from][to]
HMM_TRANS = {
    B: {B: -1e9, M: -0.9322, E: -0.5548, S: -1e9},
    M: {B: -1e9, M: -0.8872, E: -0.5764, S: -1e9},
    E: {B: -0.3748, M: -1e9, E: -1e9, S: -1.2652},
    S: {B: -0.5195, M: -1e9, E: -1e9, S: -0.9206},
}

# 发射概率默认值 (log) — 对未训练字符赋予均匀概率
_DEFAULT_EMIT_LOG = math.log(1.0 / 65536)

# ── 从词典自动训练发射概率 ──
def _train_emit_from_dict(words_with_freq: list[tuple[str, int]]) -> dict[int, dict[str, float]]:
    """根据词典中的词, 统计各位置字符出现频次, 计算发射概率."""
    counts: dict[int, defaultdict[str, int]] = {
        s: defaultdict(int) for s in range(NUM_STATES)
    }
    total: dict[int, int] = defaultdict(int)

    for word, freq in words_with_freq:
        n = len(word)
        if n == 1:
            counts[S][word] += freq
            total[S] += freq
        elif n >= 2:
            counts[B][word[0]] += freq
            total[B] += freq
            for ch in word[1:-1]:
                counts[M][ch] += freq
                total[M] += freq
            counts[E][word[-1]] += freq
            total[E] += freq

    emit: dict[int, dict[str, float]] = {}
    for state in range(NUM_STATES):
        t = total[state] or 1
        emit[state] = {ch: math.log(c / t) for ch, c in counts[state].items()}
    return emit


# ═══════════════════════════════════════════════════════
#  内置词典 (高频词, 生产环境请加载完整词典)
# ═══════════════════════════════════════════════════════

_BUILTIN_DICT: list[tuple[str, int]] = [
    # ── 单字高频 ──
    ("的", 10258736), ("一", 3978654), ("是", 6254123), ("不", 4875362),
    ("了", 5897412), ("在", 4658723), ("人", 3245678), ("有", 3125478),
    ("我", 3654789), ("他", 2345678), ("这", 2987456), ("中", 2654789),
    ("大", 2123456), ("来", 2456789), ("上", 2874563), ("国", 2547896),
    ("个", 2785634), ("到", 1987456), ("说", 1874563), ("时", 1765432),
    ("要", 1654321), ("就", 1543210), ("出", 1432109), ("会", 1321098),
    ("可", 1210987), ("也", 1109876), ("你", 1098765), ("对", 1087654),
    ("生", 1076543), ("以", 1065432), ("那", 1054321), ("和", 1043210),
    ("下", 1032109), ("自", 987654), ("之", 976543), ("年", 965432),
    ("过", 954321), ("发", 943210), ("后", 932109), ("作", 921098),
    ("里", 910987), ("用", 900876), ("道", 890765), ("行", 880654),
    ("所", 870543), ("然", 860432), ("家", 850321), ("种", 840210),
    ("事", 830109), ("成", 820098), ("方", 810987), ("多", 800876),
    ("经", 790765), ("去", 780654), ("法", 770543), ("学", 760432),
    ("如", 750321), ("都", 740210), ("同", 730109), ("现", 720098),
    ("当", 710987), ("没", 700876), ("动", 690765), ("面", 680654),
    ("起", 670543), ("看", 660432), ("定", 650321), ("天", 640210),
    ("分", 630109), ("还", 620098), ("进", 610987), ("好", 600876),
    ("小", 590765), ("部", 580654), ("其", 570543), ("些", 560432),
    ("主", 550321), ("样", 540210), ("理", 530109), ("心", 520098),
    ("她", 510987), ("本", 500876), ("前", 490765), ("开", 480654),
    ("但", 470543), ("因", 460432), ("只", 450321), ("从", 440210),
    ("想", 430109), ("实", 420098), ("日", 410987), ("意", 400876),
    ("无", 390765), ("力", 380654), ("它", 370543), ("与", 360432),
    ("长", 350321), ("把", 340210), ("机", 330109), ("十", 320098),
    ("民", 310987), ("第", 300876), ("公", 290765), ("此", 280654),
    ("已", 270543), ("工", 260432), ("使", 250321), ("新", 240210),
    ("高", 230109), ("地", 220098), ("年", 210987), ("三", 200876),
    ("最", 190765), ("于", 180654), ("二", 170543), ("能", 160432),
    ("而", 150321), ("子", 140210), ("东", 130109), ("产", 120098),
    # ── 二字词 ──
    ("中国", 5647832), ("人民", 4325678), ("国家", 3987456),
    ("社会", 3654789), ("经济", 3547896), ("政治", 2874563),
    ("文化", 2765432), ("教育", 2654321), ("科学", 2543210),
    ("技术", 2432109), ("发展", 3245678), ("建设", 2123456),
    ("管理", 2012345), ("服务", 1987456), ("信息", 1876543),
    ("网络", 1765432), ("系统", 1654321), ("研究", 1543210),
    ("分析", 1432109), ("设计", 1321098), ("开发", 1210987),
    ("实现", 1109876), ("处理", 1098765), ("应用", 1087654),
    ("使用", 1076543), ("提供", 1065432), ("支持", 1054321),
    ("产品", 1043210), ("市场", 1032109), ("企业", 1021098),
    ("公司", 1010987), ("行业", 998765), ("产业", 987654),
    ("资源", 976543), ("环境", 965432), ("能源", 954321),
    ("材料", 943210), ("问题", 1098765), ("方案", 987654),
    ("方法", 976543), ("程序", 965432), ("数据", 1109876),
    ("文件", 943210), ("软件", 1098765), ("硬件", 876543),
    ("平台", 987654), ("工具", 876543), ("汽车", 865432),
    ("工程", 854321), ("项目", 843210), ("计划", 832109),
    ("政策", 821098), ("改革", 810987), ("开放", 800876),
    ("历史", 790765), ("艺术", 780654), ("世界", 1123456),
    ("北京", 987654), ("上海", 976543), ("广州", 865432),
    ("深圳", 854321), ("学生", 876543), ("老师", 865432),
    ("学校", 854321), ("大学", 843210), ("工作", 1098765),
    ("生活", 987654), ("时间", 1109876), ("地方", 987654),
    ("关系", 876543), ("条件", 865432), ("标准", 854321),
    ("安全", 843210), ("质量", 832109), ("效率", 821098),
    ("效果", 810987), ("能力", 800876), ("知识", 790765),
    ("技能", 780654), ("经验", 770543), ("水平", 760432),
    ("程度", 750321), ("报告", 740210), ("论文", 730109),
    ("文章", 720098), ("新闻", 710987), ("媒体", 700876),
    ("电影", 690765), ("音乐", 680654), ("游戏", 670543),
    ("电脑", 660432), ("手机", 1098765), ("电话", 650321),
    ("交通", 640210), ("城市", 630109), ("农村", 620098),
    ("医院", 610987), ("医生", 600876), ("健康", 590765),
    ("食品", 580654), ("商业", 570543), ("银行", 560432),
    ("投资", 550321), ("股票", 540210), ("基金", 530109),
    ("战略", 520098), ("创新", 510987), ("增长", 500876),
    ("变化", 490765), ("影响", 480654), ("作用", 470543),
    ("意义", 460432), ("价值", 450321), ("价格", 440210),
    ("成本", 430109), ("利润", 420098), ("保护", 410987),
    ("自然", 400876), ("动物", 390765), ("植物", 380654),
    ("地球", 370543), ("空间", 360432), ("电子", 350321),
    ("通信", 340210), ("互联网", 1987456), ("计算机", 1234567),
    ("人工智能", 1876543), ("大数据", 1543210), ("区块链", 987654),
    ("云计算", 1098765), ("物联网", 876543), ("机器学习", 1234567),
    ("深度学习", 1098765), ("搜索引擎", 876543), ("电子商务", 865432),
    ("有限公司", 1543210), ("研究所", 876543),
    ("我们", 2345678), ("他们", 1876543), ("自己", 1765432),
    ("什么", 1987456), ("没有", 1876543), ("如果", 1654321),
    ("可以", 1765432), ("因为", 1543210), ("所以", 1432109),
    ("但是", 1321098), ("虽然", 1210987), ("已经", 1109876),
    ("然后", 987654), ("可能", 1098765), ("应该", 987654),
    ("需要", 1098765), ("现在", 1087654), ("这些", 976543),
    ("那些", 876543), ("如何", 987654), ("通过", 976543),
    ("之间", 876543), ("以及", 865432), ("其中", 854321),
    ("而且", 765432), ("或者", 754321), ("除了", 654321),
    ("关于", 643210), ("对于", 632109), ("根据", 621098),
    ("目前", 610987), ("同时", 600876), ("进行", 590765),
    ("开始", 580654), ("认为", 570543), ("成为", 560432),
    ("发现", 550321), ("表示", 540210), ("出现", 530109),
    ("继续", 520098), ("决定", 510987), ("准备", 500876),
    ("提高", 490765), ("推动", 480654), ("加强", 470543),
    ("建立", 460432), ("完善", 450321), ("保持", 440210),
    ("获得", 430109), ("利用", 420098), ("采取", 410987),
    ("形成", 390765), ("达到", 380654),
    ("受到", 370543), ("具有", 360432), ("产生", 350321),
    ("不仅", 340210), ("而是", 330109), ("其他", 320098),
    ("主要", 310987), ("包括", 300876), ("基本", 290765),
    ("直接", 280654), ("方面", 270543), ("重要", 260432),
    ("比较", 250321), ("全部", 240210), ("特别", 230109),
    ("最后", 220098), ("之后", 210987), ("之前", 200876),
    ("以上", 180654), ("以下", 170543),
    ("起来", 160432), ("出来", 150321), ("下来", 140210),
    ("上去", 130109), ("不会", 120098), ("不能", 110987),
    ("不要", 100876), ("不是", 98765), ("不到", 97654),
    ("不好", 96543), ("不同", 95432), ("一定", 94321),
    ("一个", 98765), ("一些", 97654), ("一种", 96543),
    ("这个", 98765), ("那个", 97654), ("每个", 86543),
    ("所有", 85432), ("任何", 84321), ("唯一", 73210),
    ("简单", 87654), ("复杂", 76543), ("容易", 65432),
    ("困难", 64321), ("快速", 63210), ("慢慢", 52109),
    ("完全", 87654), ("正确", 76543), ("错误", 65432),
    ("明显", 64321), ("清楚", 53210), ("详细", 52109),
    ("具体", 64321), ("全面", 53210), ("深入", 52109),
]

# ═══════════════════════════════════════════════════════
#  停用词
# ═══════════════════════════════════════════════════════

_BUILTIN_STOPWORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
    "它", "们", "那", "些", "么", "把", "被", "让", "给", "从",
    "向", "对", "为", "以", "与", "及", "等", "而", "或", "但",
    "如", "所", "之", "其", "但", "如果", "因为", "所以", "但是",
    "虽然", "然而", "而且", "或者", "可以", "可能", "应该", "需要",
    "这个", "那个", "什么", "怎么", "如何", "哪个", "哪里", "多少",
    "这些", "那些", "一些", "每个", "任何", "所有", "其它", "其他",
    "啊", "呀", "吧", "呢", "吗", "哦", "嗯", "哈", "嘛",
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "can", "shall",
    "of", "in", "to", "for", "with", "on", "at", "from", "by",
    "and", "or", "not", "no", "it", "its", "this", "that",
    "as", "if", "but", "so", "than", "too", "very", "just",
}


# ═══════════════════════════════════════════════════════
#  Tokenizer 类
# ═══════════════════════════════════════════════════════

class ChineseTokenizer:
    """专业中文分词器: Trie + DAG + DP + HMM."""

    def __init__(self):
        self._root = TrieNode()
        self._total_freq = 0
        self._max_word_len = config.TOKENIZER["max_word_len"]
        self._hmm_enabled = config.TOKENIZER["hmm_enabled"]
        self._stopwords: set[str] = set()
        self._emit: dict[int, dict[str, float]] | None = None

        # 加载词典
        self._load_builtin_dict()
        self._load_external_dict(config.TOKENIZER["dict_path"])
        self._total_freq = self._root.freq  # root.freq 累计所有词频

        # 从词典训练 HMM 发射概率
        if self._hmm_enabled:
            self._emit = _train_emit_from_dict(_BUILTIN_DICT)

        # 加载停用词
        self._stopwords = set(_BUILTIN_STOPWORDS)
        self._load_stopwords(config.TOKENIZER["stopwords_path"])

    # ── 词典加载 ──────────────────────────────────────

    def _insert(self, word: str, freq: int):
        node = self._root
        for ch in word:
            if ch not in node.children:
                node.children[ch] = TrieNode()
            node = node.children[ch]
        node.is_word = True
        node.freq = freq
        self._root.freq += freq

    def _load_builtin_dict(self):
        for word, freq in _BUILTIN_DICT:
            self._insert(word, freq)

    def _load_external_dict(self, path: str):
        """加载外部词典文件. 格式: 词语 频率 [词性]  (每行)"""
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        word = parts[0]
                        try:
                            freq = int(parts[1])
                        except ValueError:
                            continue
                        if 1 <= len(word) <= self._max_word_len:
                            self._insert(word, freq)
        except OSError:
            pass

    def _load_stopwords(self, path: str):
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    w = line.strip()
                    if w:
                        self._stopwords.add(w)
        except OSError:
            pass

    # ── DAG 构建 ──────────────────────────────────────

    def _get_dag(self, sentence: str) -> dict[int, list[tuple[int, int]]]:
        """
        构建 DAG: dag[i] = [(j, freq), ...]
        表示 sentence[i:j+1] 是一个词, freq 为词频.
        """
        n = len(sentence)
        dag: dict[int, list[tuple[int, int]]] = {i: [] for i in range(n)}
        for i in range(n):
            node = self._root
            for j in range(i, min(n, i + self._max_word_len)):
                ch = sentence[j]
                if ch not in node.children:
                    break
                node = node.children[ch]
                if node.is_word:
                    dag[i].append((j, node.freq))
            if not dag[i]:
                # 单字作为兜底
                ch = sentence[i]
                fallback_node = self._root.children.get(ch)
                dag[i].append((i, fallback_node.freq if fallback_node and fallback_node.is_word else 1))
        return dag

    # ── DP 最大概率路径 ───────────────────────────────

    def _calc_route(self, sentence: str, dag: dict[int, list[tuple[int, int]]]) -> list[tuple[int, int]]:
        """
        动态规划: 从右向左求最大对数概率路径.
        返回 [(start, end), ...] 最优切分方案.
        """
        n = len(sentence)
        total = self._total_freq or 1
        log_total = math.log(total)

        # route[i] = (log_prob, next_position)
        route: list[tuple[float, int]] = [(0.0, 0)] * (n + 1)
        for i in range(n - 1, -1, -1):
            best = max(
                (
                    (math.log(freq or 1) - log_total + route[j + 1][0], j)
                    for j, freq in dag[i]
                ),
                key=lambda x: x[0],
            )
            route[i] = best

        # 回溯
        result: list[tuple[int, int]] = []
        idx = 0
        while idx < n:
            _, end = route[idx]
            result.append((idx, end))
            idx = end + 1
        return result

    # ── HMM Viterbi ───────────────────────────────────

    def _hmm_cut(self, sentence: str) -> list[str]:
        """对一个未登录片段使用 HMM Viterbi 解码, 返回分词结果."""
        n = len(sentence)
        if n == 0:
            return []
        if n == 1:
            return [sentence]

        emit = self._emit if self._emit else {}

        # Viterbi
        v: dict[int, tuple[float, list[int]]] = {}
        for s in range(NUM_STATES):
            emit_p = emit.get(s, {}).get(sentence[0], _DEFAULT_EMIT_LOG)
            v[s] = (HMM_START[s] + emit_p, [s])

        for t in range(1, n):
            new_v: dict[int, tuple[float, list[int]]] = {}
            for cur_s in range(NUM_STATES):
                emit_p = emit.get(cur_s, {}).get(sentence[t], _DEFAULT_EMIT_LOG)
                best = (-1e18, [])
                for prev_s in range(NUM_STATES):
                    prev_p, prev_path = v[prev_s]
                    trans_p = HMM_TRANS[prev_s].get(cur_s, -1e9)
                    p = prev_p + trans_p + emit_p
                    if p > best[0]:
                        best = (p, prev_path + [cur_s])
                new_v[cur_s] = best
            v = new_v

        # 取最优路径
        best_state = max(v, key=lambda s: v[s][0])
        path = v[best_state][1]

        # 根据 BMES 路径切分
        result = []
        buf = ""
        for i, st in enumerate(path):
            buf += sentence[i]
            if st in (E, S):
                result.append(buf)
                buf = ""
        if buf:
            result.append(buf)

        return result

    # ── 主切分方法 ────────────────────────────────────

    def _cut_detail(self, sentence: str) -> list[str]:
        """对一个纯中文句子做完整分词 (DP + HMM)."""
        dag = self._get_dag(sentence)
        route = self._calc_route(sentence, dag)

        result: list[str] = []
        for start, end in route:
            word = sentence[start:end + 1]
            # 如果词长 > 1 且不在词典中, 尝试 HMM
            if len(word) > 1 and self._hmm_enabled:
                node = self._root
                found = True
                for ch in word:
                    if ch not in node.children:
                        found = False
                        break
                    node = node.children[ch]
                if not (found and node.is_word):
                    result.extend(self._hmm_cut(word))
                    continue
            result.append(word)
        return result

    def cut(self, text: str) -> list[str]:
        """
        对文本进行分词, 返回 token 列表 (已过滤停用词).

        处理流程:
          1. 用正则把文本拆成 中文片段 / 英文数字片段 / 其他
          2. 中文片段走 DP + HMM
          3. 英文数字原样输出 (小写)
          4. 过滤停用词和空串
        """
        # 匹配: 连续中文 | 连续英文/数字 | 其他
        pattern = re.compile(
            r"([\u4e00-\u9fff\u3400-\u4dbf]+)"   # 中文
            r"|([a-zA-Z0-9]+(?:\.[a-zA-Z0-9]+)*)"  # 英文/数字/版本号
            r"|(\S)",                                 # 其他单字符
        )

        tokens: list[str] = []
        for m in pattern.finditer(text):
            cn_group = m.group(1)
            en_group = m.group(2)
            if cn_group:
                tokens.extend(self._cut_detail(cn_group))
            elif en_group:
                tokens.append(en_group.lower())

        # 过滤停用词、空白、纯标点
        result = []
        for t in tokens:
            t = t.strip()
            if not t or t in self._stopwords:
                continue
            # 跳过纯标点
            if re.match(r"^[\W_]+$", t):
                continue
            result.append(t)
        return result


# ── 模块级单例 ────────────────────────────────────────
_tokenizer: ChineseTokenizer | None = None

def get_tokenizer() -> ChineseTokenizer:
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = ChineseTokenizer()
    return _tokenizer

def tokenize(text: str) -> list[str]:
    return get_tokenizer().cut(text)
