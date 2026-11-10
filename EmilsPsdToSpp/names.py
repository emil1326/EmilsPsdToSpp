"""Layer names: Japanese -> English, and which layers to leave out."""
import re

# Common words in Japanese avatar PSDs. Anything not listed keeps its name.
WORDS = {
    "頭": "Head", "顔": "Face", "目": "Eyes", "瞳": "Pupil", "白目": "Eye White",
    "眉": "Eyebrows", "まつ毛": "Eyelashes", "口": "Mouth", "口内": "Mouth Inside",
    "歯": "Teeth", "舌": "Tongue", "鼻": "Nose", "耳": "Ears", "髪": "Hair",
    "前髪": "Bangs", "後ろ髪": "Back Hair", "体": "Body", "胴体": "Body", "手": "Hands",
    "足": "Feet", "脚": "Legs", "腕": "Arms", "爪": "Claws", "尻尾": "Tail",
    "しっぽ": "Tail", "羽": "Feathers", "翼": "Wings", "毛": "Fur", "首毛": "Neck Fur",
    "背中の毛": "Back Fur", "胸毛": "Chest Fur", "肉球": "Paw Pads", "角": "Horns",
    "服": "Clothes", "影": "Shadow", "光": "Light", "ハイライト": "Highlight",
    "線画": "Lineart", "下地": "Base", "ベース": "Base", "色": "Color", "模様": "Pattern",
    "柄": "Pattern", "トラ柄": "Tiger Stripes", "ヒョウ柄": "Leopard Spots",
    "グラデーション": "Gradient", "マスク": "Mask", "漫符": "Emote Marks",
    "頬": "Cheeks", "チーク": "Blush", "ほっぺ": "Cheeks", "涙": "Tears",
    "汗": "Sweat", "星": "Star", "ハート": "Heart", "背景": "Background",
    # romaji some authors use
    "Manpu": "Emote Marks", "Guruguru": "Swirl", "Karada": "Body", "Atama": "Head",
}

_JP = re.compile(r"[぀-ヿ㐀-鿿＀-￯]")
DEFAULT_SKIP = r"^(uv ?map|uv|guides?)$|_PNG?$"


def has_japanese(s):
    return bool(_JP.search(s))


def translate(raw):
    name = raw.replace("\x00", "").strip()
    name = name.translate({c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)})  # full-width -> ASCII
    m = re.fullmatch(r"レイヤー\s*(\d+)", name)
    if m:
        return f"Layer {m.group(1)}"
    if name in WORDS:
        return WORDS[name]
    # "日本語-English" or "English-日本語": keep the English half
    if "-" in name:
        parts = [p.strip() for p in name.split("-", 1)]
        en = [p for p in parts if p and not has_japanese(p)]
        jp = [p for p in parts if has_japanese(p)]
        if en and jp:
            return WORDS.get(en[0], en[0])
    # "影 2", "目_影": swap the known words, keep the rest
    return re.sub(r"[぀-ヿ㐀-鿿]+",
                  lambda m: WORDS.get(m.group(0), m.group(0)), name)


def make_skip(pattern):
    if not pattern:
        return lambda raw, name: False
    rx = re.compile(pattern, re.IGNORECASE)
    return lambda raw, name: bool(rx.search(name) or rx.search(raw.replace("\x00", "").strip()))
