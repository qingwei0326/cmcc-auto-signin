"""guandan_rules.py —— 掼蛋（2v2）牌面编码、牌型识别、出牌决策。

全部规则逆向自咪咕掼蛋客户端 `assets/scripts/index.js` 里的
`eGdType` / `GdPok` / `GdType.canOver`，与线上服务端一致；见 docs/guandan.md。

牌值编码（服务端 WebSocket 直接用这套整数）：

    value = 34 + 32 * 花色组 + (点数 - 2)

    花色组  0=♥  1=♦  2=♣  3=♠   （客户端 pos2col=[0,♥,♦,♣,♠]）
    点数    2..14（14=A）
    大小王  16=小王  17=大王

    例：♥2=34  ♦2=66  ♣2=98  ♠2=130
        ♥A=46  ♦A=78  ♣A=110 ♠A=142

    红桃级牌（打 2 时的 ♥2）就是客户端里的 Series = 32+host，是逢人配万能牌。

级牌（本局打的点数，下称 host）：点数为 host 的牌在比大小时提到 15，
只低于大小王。红桃 host 是「逢人配」万能牌，本模块暂按普通牌处理（见 TODO）。

牌型编号 eGdType（出牌请求第 5 个字段 `type` 就是它）：

    0  不出        1  单张      2  对子       3  三张
    4  三带二      5  顺子(5张)  6  连对(3对)  7  钢板(2个三张)
    8  四张炸      9  五张炸     10 同花顺     11 六张炸
    12 七张炸      13 四张二连炸 14 八张炸     15 九张炸
    16 四张三连炸  17 十张炸     20 四大天王

比大小：同牌型比 order；不同牌型只有炸弹（编号 >=8）能压，且编号更大才行。
"""

from __future__ import annotations

from collections import Counter

# ---------------------------------------------------------------- 牌面编码

SUITS = ["♥", "♦", "♣", "♠"]
SUIT_NAMES = ["红桃", "方块", "梅花", "黑桃"]
RANK_NAMES = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]

SMALL_JOKER = 16
BIG_JOKER = 17

HOST_PROMOTED = 15          # 级牌在比大小时的权重
MAX_RANK = 14               # A

# ---------------------------------------------------------------- 牌型常量

T_PASS = 0
T_SINGLE = 1
T_PAIR = 2
T_TRIPLE = 3
T_TRIPLE2 = 4
T_STRAIGHT = 5
T_PAIRSEQ = 6
T_PLATE = 7
T_BOMB4 = 8
T_BOMB5 = 9
T_FLUSH = 10
T_BOMB6 = 11
T_BOMB7 = 12
T_BOMB8 = 14
T_BOMB9 = 15
T_JOKER4 = 20

#: 同点数牌堆 -> 牌型编号
BOMB_BY_SIZE = {4: T_BOMB4, 5: T_BOMB5, 6: T_BOMB6, 7: T_BOMB7, 8: T_BOMB8, 9: T_BOMB9}

TYPE_NAMES = {
    T_PASS: "不出",
    T_SINGLE: "单张",
    T_PAIR: "对子",
    T_TRIPLE: "三张",
    T_TRIPLE2: "三带二",
    T_STRAIGHT: "顺子",
    T_PAIRSEQ: "连对",
    T_PLATE: "钢板",
    T_BOMB4: "四张炸",
    T_BOMB5: "五张炸",
    T_FLUSH: "同花顺",
    T_BOMB6: "六张炸",
    T_BOMB7: "七张炸",
    T_BOMB8: "八张炸",
    T_BOMB9: "九张炸",
    T_JOKER4: "四大天王",
}


def card_value(suit: int, rank: int) -> int:
    """花色组 0..3 + 点数 2..14 -> 服务端牌值。"""
    if not (0 <= suit <= 3 and 2 <= rank <= MAX_RANK):
        raise ValueError(f"非法花色/点数: {suit}/{rank}")
    return 34 + 32 * suit + (rank - 2)


def is_joker(v: int) -> bool:
    return v < 34


def rank_of(v: int) -> int:
    """牌值 -> 点数。王返回 16/17。"""
    return v & 31


def suit_of(v: int) -> int:
    """牌值 -> 花色组 0..3。王返回 -1。"""
    if is_joker(v):
        return -1
    return (v - 34) // 32


def card_text(v: int) -> str:
    if v == SMALL_JOKER:
        return "小王"
    if v == BIG_JOKER:
        return "大王"
    if is_joker(v):
        return f"?王{v}"
    r = rank_of(v)
    s = suit_of(v)
    if not (2 <= r <= MAX_RANK) or not (0 <= s <= 3):
        return f"?{v}"
    return f"{SUITS[s]}{RANK_NAMES[r - 2]}"


def cards_text(cards) -> str:
    return " ".join(card_text(c) for c in cards)


def order_of(v: int, host: int) -> int:
    """比大小用的权重：级牌提到 15，王 16/17，其余按点数。"""
    r = rank_of(v)
    if r >= SMALL_JOKER:
        return r
    if r == host:
        return HOST_PROMOTED
    return r


# ---------------------------------------------------------------- 牌型

class Play(object):
    """一手牌：牌型编号 + 比较权重 + 具体牌。"""

    __slots__ = ("type", "order", "cards")

    def __init__(self, type_: int, order: int, cards):
        self.type = type_
        self.order = order
        self.cards = tuple(cards)

    def __repr__(self):
        return f"<{TYPE_NAMES.get(self.type, self.type)} {cards_text(self.cards)}>"

    def __eq__(self, other):
        return (isinstance(other, Play) and self.type == other.type
                and self.order == other.order and self.cards == other.cards)

    def __hash__(self):
        return hash((self.type, self.order, self.cards))

    @property
    def name(self) -> str:
        return TYPE_NAMES.get(self.type, f"牌型{self.type}")

    @property
    def is_bomb(self) -> bool:
        return self.type >= T_BOMB4


def _consecutive(ranks) -> bool:
    """升序去重后连续。"""
    return all(ranks[i] + 1 == ranks[i + 1] for i in range(len(ranks) - 1))


def _straight_orders(ranks):
    """5 张顺子的候选首牌。A 可作 1（A2345）也可作 14（10JQKA）。"""
    out = []
    r = sorted(ranks)
    if len(r) == 5 and _consecutive(r) and r[-1] <= MAX_RANK:
        out.append(r[-1])
    # A2345：把 A 当 1
    if r == [2, 3, 4, 5, MAX_RANK]:
        out.append(5)
    return out


def is_wild(v: int, host: int) -> bool:
    """红桃级牌 = 逢人配（万能牌）。客户端里等于 Series = 32 + host。"""
    return v == 32 + host


def _natural(cards, host: int):
    """不考虑万能牌的牌型识别。"""
    cards = list(cards)
    n = len(cards)
    if n == 0:
        return None
    ranks = [rank_of(c) for c in cards]
    cnt = Counter(ranks)
    uniq = sorted(cnt)
    kind = len(uniq)

    # ---- 四大天王（两小王 + 两大王）
    if n == 4 and cnt.get(SMALL_JOKER, 0) == 2 and cnt.get(BIG_JOKER, 0) == 2:
        return Play(T_JOKER4, BIG_JOKER, cards)

    # ---- 同点数：单/对/三/炸
    if kind == 1:
        r = uniq[0]
        if r >= SMALL_JOKER:
            if n == 1:
                return Play(T_SINGLE, r, cards)
            if n == 2:
                return Play(T_PAIR, r, cards)
            return None
        if n == 1:
            return Play(T_SINGLE, order_of(cards[0], host), cards)
        if n == 2:
            return Play(T_PAIR, order_of(cards[0], host), cards)
        if n == 3:
            return Play(T_TRIPLE, order_of(cards[0], host), cards)
        t = BOMB_BY_SIZE.get(n)
        if t:
            return Play(t, order_of(cards[0], host), cards)
        return None

    # ---- 三张带二（三张 + 任意两张，客户端就是这么宽的）
    if n == 5 and max(cnt.values()) >= 3:
        triple = max(cnt, key=lambda r: (cnt[r], r))
        if triple < SMALL_JOKER:
            rep = next(c for c in cards if rank_of(c) == triple)
            return Play(T_TRIPLE2, order_of(rep, host), cards)

    # ---- 顺子 / 同花顺
    if n == 5 and kind == 5:
        tops = _straight_orders(ranks)
        if tops:
            suits = {suit_of(c) for c in cards}
            if len(suits) == 1:
                return Play(T_FLUSH, max(tops), cards)
            return Play(T_STRAIGHT, max(tops), cards)

    # ---- 连对（3 连对）/ 钢板（2 个三张）
    if n == 6 and kind == 3 and sorted(cnt.values()) == [2, 2, 2] and _consecutive(uniq):
        return Play(T_PAIRSEQ, uniq[-1], cards)
    if n == 6 and kind == 2 and sorted(cnt.values()) == [3, 3] and _consecutive(uniq):
        return Play(T_PLATE, uniq[-1], cards)

    return None


def classify(cards, host: int, hint=None, wild=True):
    """识别一手牌。hint 是服务端给的牌型编号，可以用它反向确定万能牌的用法。

    红桃级牌是逢人配：先按原样识别，识别不出（或与服务端编号不符）时，
    把它替换成任意点数再试，取能对上 hint 的那一种。
    """
    cards = list(cards)
    nat = _natural(cards, host)
    if not wild:
        return nat
    if nat is not None and (hint is None or nat.type == hint):
        return nat
    wild_idx = [i for i, c in enumerate(cards) if is_wild(c, host)]
    if not wild_idx:
        return nat
    best = nat
    for combo in _rank_combos(len(wild_idx)):
        trial = list(cards)
        for k, i in enumerate(wild_idx):
            trial[i] = card_value(0, combo[k])
        p = _natural(trial, host)
        if p is None:
            continue
        if hint is not None and p.type != hint:
            continue
        if best is None or (p.type, p.order) > (best.type, best.order):
            p.cards = tuple(cards)      # 记原始牌，替换只用来算大小
            best = p
    return best


def _rank_combos(k):
    """k 张万能牌的点数组合（2..14）。"""
    if k <= 0:
        return [()]
    out = []
    base = _rank_combos(k - 1)
    for tail in base:
        for r in range(2, MAX_RANK + 1):
            out.append((r,) + tail)
    return out


def can_over(a: Play, b: Play) -> bool:
    """a 能否压过 b。"""
    if a is None or b is None or b.type == T_PASS:
        return a is not None
    if a.type == b.type:
        return a.order > b.order
    return a.type >= T_BOMB4 and a.type > b.type


# ---------------------------------------------------------------- 候选枚举

def _pick(cards, rank, k, used):
    """从 cards 里取 k 张 rank 的牌（跳过 used 下标）。"""
    out = []
    for i, c in enumerate(cards):
        if i in used:
            continue
        if rank_of(c) == rank:
            out.append(i)
            if len(out) == k:
                return out
    return None


def all_plays(cards, host: int, limit_len=None):
    """枚举这手牌里所有合法牌型（不枚举大于 8 张的连炸）。

    limit_len 可限制长度集合，用于「只需压过 N 张」的快速路径。
    """
    cards = list(cards)
    out = []
    wild = {i for i, c in enumerate(cards) if is_wild(c, host)}
    idx_by_rank = {}
    for i, c in enumerate(cards):
        idx_by_rank.setdefault(rank_of(c), []).append(i)

    wanted = limit_len or set()

    # 单张 / 对子 / 三张 / 炸弹
    for r, idxs in idx_by_rank.items():
        for k in (1, 2, 3, 4, 5, 6, 7, 8, 9):
            if k > len(idxs):
                break
            if wanted and k not in wanted:
                continue
            sub = [cards[i] for i in idxs[:k]]
            p = classify(sub, host)
            if p:
                out.append(p)
    # 四大天王
    if not wanted or 4 in wanted:
        if len(idx_by_rank.get(SMALL_JOKER, [])) >= 2 and len(idx_by_rank.get(BIG_JOKER, [])) >= 2:
            sub = [cards[i] for i in idx_by_rank[SMALL_JOKER][:2] + idx_by_rank[BIG_JOKER][:2]]
            out.append(Play(T_JOKER4, BIG_JOKER, sub))

    # 三带二
    if not wanted or 5 in wanted:
        pairs = [r for r in idx_by_rank if len(idx_by_rank[r]) >= 2 and r < SMALL_JOKER]
        for tr in list(idx_by_rank):
            if tr >= SMALL_JOKER or len(idx_by_rank[tr]) < 3:
                continue
            for pr in pairs:
                if pr == tr:
                    continue
                pick = idx_by_rank[tr][:3] + idx_by_rank[pr][:2]
                if any(i in wild for i in pick):
                    continue
                sub = [cards[i] for i in pick]
                p = classify(sub, host)
                if p:
                    out.append(p)

    # 顺子 / 同花顺 / 连对 / 钢板
    plain = [r for r in idx_by_rank if r < SMALL_JOKER]
    have = set(plain)
    if not wanted or 5 in wanted:
        for top in range(5, MAX_RANK + 1):
            seq = [top - 4, top - 3, top - 2, top - 1, top]
            if not set(seq) <= have:
                continue
            sub = [cards[idx_by_rank[r][0]] for r in seq]
            p = classify(sub, host)
            if p:
                out.append(p)
            # 同花顺：同一花色
            for s in range(4):
                got = []
                for r in seq:
                    for i in idx_by_rank[r]:
                        if suit_of(cards[i]) == s:
                            got.append(i)
                            break
                    else:
                        got = None
                        break
                if got:
                    p = classify([cards[i] for i in got], host)
                    if p and p.type == T_FLUSH:
                        out.append(p)
        # A2345
        if {2, 3, 4, 5, MAX_RANK} <= have:
            sub = [cards[idx_by_rank[r][0]] for r in (MAX_RANK, 2, 3, 4, 5)]
            p = classify(sub, host)
            if p:
                out.append(p)

    if not wanted or 6 in wanted:
        for top in range(4, MAX_RANK + 1):
            seq = [top - 2, top - 1, top]
            if not set(seq) <= have:
                continue
            if all(len(idx_by_rank[r]) >= 2 for r in seq):
                sub = [cards[i] for r in seq for i in idx_by_rank[r][:2]]
                
                p = classify(sub, host)
                if p:
                    out.append(p)
        for top in range(3, MAX_RANK + 1):
            seq = [top - 1, top]
            if not set(seq) <= have:
                continue
            if all(len(idx_by_rank[r]) >= 3 for r in seq):
                sub = [cards[i] for r in seq for i in idx_by_rank[r][:3]]
                p = classify(sub, host)
                if p:
                    out.append(p)

    # 去重
    seen = set()
    uniq = []
    for p in out:
        key = (p.type, p.order, p.cards)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    uniq.sort(key=lambda p: (p.is_bomb, p.type, p.order, len(p.cards)))
    return uniq


def find_beat(cards, prev: Play, host: int):
    """找出能压过 prev 的最小一手牌；压不过返回 None。"""
    if prev is None or prev.type == T_PASS:
        return None
    n = len(prev.cards)
    cands = [p for p in all_plays(cards, host, limit_len={n, 4, 5, 6, 7, 8, 9})
             if can_over(p, prev)]
    if not cands:
        return None
    # 同牌型同点数时，优先「花的牌最小」的那一组（三带二带最小的对子）
    cands.sort(key=lambda p: (p.is_bomb, p.type, p.order, len(p.cards),
                              sum(order_of(c, host) for c in p.cards)))
    return cands[0]


# ---------------------------------------------------------------- 拆牌 / 决策

def decompose(cards, host: int):
    """把手牌拆成尽量少的组合，返回 Play 列表（贪心，够用）。

    顺序：钢板 -> 连对 -> 顺子 -> 三张（带对子成三带二）-> 炸弹/对子 -> 单张。
    万能牌（红桃级牌）单独留着，只在最后当单张走，避免被随便拆掉。
    """
    cards = list(cards)
    wilds = [c for c in cards if is_wild(c, host)]
    pool = [c for c in cards if not is_wild(c, host)]
    groups = []

    def remove(sublist):
        for c in sublist:
            pool.remove(c)

    def indices(pred):
        return [i for i, c in enumerate(pool) if pred(c)]

    for _ in range(40):
        cnt = Counter(rank_of(c) for c in pool if not is_joker(c))
        done = False
        for top in range(3, MAX_RANK + 1):
            if cnt.get(top - 1, 0) >= 3 and cnt.get(top, 0) >= 3:
                sub = []
                for r in (top - 1, top):
                    sub += [pool[i] for i in [j for j, c in enumerate(pool) if rank_of(c) == r][:3]]
                groups.append(sub)
                remove(sub)
                done = True
                break
        if done:
            continue
        for top in range(4, MAX_RANK + 1):
            if all(cnt.get(top - k, 0) >= 2 for k in (0, 1, 2)):
                sub = []
                for r in (top - 2, top - 1, top):
                    sub += [pool[i] for i in [j for j, c in enumerate(pool) if rank_of(c) == r][:2]]
                groups.append(sub)
                remove(sub)
                done = True
                break
        if done:
            continue
        for top in range(5, MAX_RANK + 1):
            seq = [top - 4, top - 3, top - 2, top - 1, top]
            if all(cnt.get(r, 0) >= 1 for r in seq):
                sub = []
                for r in seq:
                    sub.append([c for c in pool if rank_of(c) == r][0])
                groups.append(sub)
                remove(sub)
                done = True
                break
        if not done:
            break

    cnt = Counter(rank_of(c) for c in pool)
    triples = [r for r in sorted(cnt) if cnt[r] == 3 and r < SMALL_JOKER]
    pair_rank = [r for r in sorted(cnt) if cnt[r] == 2 and r < SMALL_JOKER]
    for tr in triples:
        sub = [c for c in pool if rank_of(c) == tr][:3]
        mate = None
        for pr in pair_rank:
            if pr == tr:
                continue
            cand = [c for c in pool if rank_of(c) == pr][:2]
            if len(cand) == 2:
                mate = cand
                pair_rank.remove(pr)
                break
        if mate:
            sub = sub + mate
        groups.append(sub)
        remove(sub)

    cnt = Counter(rank_of(c) for c in pool)
    for r in sorted(cnt):
        if cnt[r] >= 2:
            k = cnt[r] if cnt[r] >= 4 else 2
            k = min(k, 9)
            sub = [c for c in pool if rank_of(c) == r][:k]
            groups.append(sub)
            remove(sub)

    while pool:
        groups.append([pool.pop(0)])
    # 万能牌最后当单张走
    for w in wilds:
        groups.append([w])

    plays = []
    for g in groups:
        p = classify(g, host)
        if p:
            plays.append(p)
        else:
            # 拆不动就退化成一单一单出
            for c in g:
                q = classify([c], host, wild=False)
                if q:
                    plays.append(q)
    return plays


def choose_lead(cards, host: int, partner_cards_left=None):
    """轮到我首出时的选择。"""
    plays = decompose(cards, host)
    if not plays:
        return None
    n = len(cards)
    # 只剩炸弹或很少牌时直接甩炸弹
    non_bomb = [p for p in plays if not p.is_bomb]
    if n <= 5 and any(p.is_bomb for p in plays):
        bombs = sorted([p for p in plays if p.is_bomb], key=lambda p: p.type)
        return bombs[0]
    if not non_bomb:
        return sorted(plays, key=lambda p: p.type)[0]
    # 优先低点数；同点数优先张数多（把散牌一次走掉）
    # 先甩「点数不高但张数多」的组合（顺子/连对/钢板），比一张张磨更快
    non_bomb.sort(key=lambda p: (0 if p.order <= 8 else 1, -len(p.cards), p.order, p.type))
    return non_bomb[0]


def choose_follow(cards, prev: Play, host: int, partner: bool = False,
                  partner_left=None, opp_left=None):
    """跟牌决策：返回要出的 Play，或 None 表示不出。"""
    if prev is None or prev.type == T_PASS:
        return choose_lead(cards, host)
    # 队友出的牌够大就别压
    if partner and prev.order >= 12:
        return None
    cand = find_beat(cards, prev, host)
    if cand is None:
        return None
    # 只有炸弹能压：牌还多就先留着
    if cand.is_bomb and prev.order < 10 and len(cards) > 8 and not partner:
        return None
    # 队友出的牌不压（除非我能一把走完）
    if partner and cand.is_bomb:
        return None
    return cand


def play_to_wire(p: Play):
    """(牌型编号, 牌值列表) —— 出牌请求要用。"""
    return p.type, list(p.cards)
