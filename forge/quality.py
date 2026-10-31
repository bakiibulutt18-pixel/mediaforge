"""Otomatik kalite kontrol. Yayın kararı: puan >= eşik VE kritik sorun yok.

Kontroller gerçek ölçümlerdir (örtüşme, sayı doğrulama, kaynak kapsamı, medya bütünlüğü);
LLM incelemesi ise 'model tahmini' olarak ayrı etiketlenir. Telif/politika riski asla 0 varsayılmaz.
"""
from __future__ import annotations

import re

from . import llm

BANNED_PHRASES = [
    "şok oldu", "inanamayacaksınız", "doktorlar bunu sakladı", "kesin kazanç", "garanti kazanç",
    "bu videoyu izlemeden", "asla yapmayın", "%100 garanti",
]
SPELLED_NUMBERS = re.compile(r"\b(yüz|bin|binlerce|milyon|milyonlarca|milyar|milyarlarca|trilyon)\b", re.I)
POLICY_KEYWORDS =["intihar", "kendine zarar", "silah yapımı", "bomba yapımı", "uyuşturucu üretimi", "nefret söylemi"]


def shingles(text: str, n: int = 5) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(len(words) - n + 1, 0))}


def overlap(a: str, b: str) -> float:
    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def numbers_in(text: str) -> set[str]:
    return {re.sub(r"[.,\s]", "", n) for n in re.findall(r"\d[\d.,]*\d|\d", text)}


class Report:
    def __init__(self):
        self.score = 100.0
        self.critical: list[str] = []
        self.issues: list[str] = []
        self.info: dict = {}

    def penalize(self, pts: float, msg: str, critical: bool = False):
        self.score = max(self.score - pts, 0)
        (self.critical if critical else self.issues).append(msg)

    def passed(self, threshold: float) -> bool:
        return not self.critical and self.score >= threshold

    def as_dict(self) -> dict:
        return {"score": round(self.score, 1), "critical": self.critical, "issues": self.issues, "info": self.info}


def check_script(script_text: str, scenes: list[dict], previous_texts: list[str], *, source_text: str = "",
                 require_sources: bool = False, valid_source_ids: set[int] | None = None) -> Report:
    r = Report()
    low = script_text.lower()
    for ph in BANNED_PHRASES:
        if ph in low:
            r.penalize(8, f"clickbait/abartılı ifade: '{ph}'")
    for kw in POLICY_KEYWORDS:
        if kw in low:
            r.penalize(0, f"politika riski anahtar kelime: '{kw}' (manuel inceleme gerekir)", critical=True)

    worst = max((overlap(script_text, p) for p in previous_texts), default=0.0)
    r.info["max_overlap_with_previous"] = round(worst, 3)
    if worst > 0.35:
        r.penalize(40, f"önceki içeriklerle yüksek örtüşme ({worst:.0%})", critical=True)
    elif worst > 0.2:
        r.penalize(10, f"önceki içeriklerle orta örtüşme ({worst:.0%})")

    if not scenes:
        r.penalize(100, "sahne yok", critical=True)
    for i, s in enumerate(scenes):
        if not (s.get("text") or "").strip():
            r.penalize(10, f"sahne {i + 1}: boş anlatım")
        if not (s.get("visual_query") or s.get("visual_prompt") or s.get("chart")):
            r.penalize(4, f"sahne {i + 1}: görsel talimatı yok")

    if source_text:
        src_nums = numbers_in(source_text)
        unmatched = []
        for i, s in enumerate(scenes):
            for n in numbers_in(s.get("text", "")):
                if len(n) >= 3 and n not in src_nums:  # yıl/miktar gibi anlamlı sayılar
                    unmatched.append((i + 1, n))
        r.info["unverified_numbers"] = unmatched[:20]
        if unmatched:
            r.penalize(min(5 * len(unmatched), 30), f"{len(unmatched)} sayı kaynak metinde bulunamadı: {unmatched[:5]}")

    # Yazıyla yazılmış büyük sayılar sayı doğrulamasından kaçar; rakamla yazılması istenir.
    spelled = SPELLED_NUMBERS.findall(script_text)
    if spelled:
        r.info["spelled_numbers"] = spelled[:10]
        r.penalize(min(3 * len(spelled), 12), f"yazıyla yazılmış sayılar doğrulanamaz ({', '.join(spelled[:4])}); rakamla yazılmalı")

    if require_sources:
        cited = 0
        for i, s in enumerate(scenes):
            ids = set(s.get("source_ids") or [])
            if valid_source_ids is not None and ids - valid_source_ids:
                r.penalize(5, f"sahne {i + 1}: geçersiz kaynak numarası {sorted(ids - valid_source_ids)}")
            cited += bool(ids)
        cov = cited / max(len(scenes), 1)
        r.info["source_coverage"] = round(cov, 2)
        if cov < 0.5:
            r.penalize(25, f"sahnelerin yalnızca %{cov * 100:.0f}'i kaynağa bağlı")
    return r


def llm_review(title: str, script_text: str, extra: str = "") -> dict:
    """Model tahmini: yanıltıcılık, politika, başlık-içerik uyumu. 0=sorun yok, 10=çok riskli."""
    prompt = (
        "Aşağıdaki video içeriğini YouTube/Instagram politikaları ve yayıncı etiği açısından incele.\n"
        f"BAŞLIK: {title}\n{extra}\nSENARYO:\n{llm.wrap_untrusted('senaryo', script_text, 9000)}\n\n"
        'Sadece JSON döndür: {"misleading": 0-10, "policy_risk": 0-10, "title_matches_content": true/false, '
        '"factual_red_flags": ["..."], "notes": "kısa not"}'
    )
    return llm.generate_json(prompt, task="qc_review", temperature=0.2, required=("misleading", "policy_risk"))


def apply_review(r: Report, review: dict) -> None:
    r.info["llm_review"] = review
    if review.get("policy_risk", 0) >= 6:
        r.penalize(30, f"politika riski yüksek (model tahmini {review['policy_risk']}/10)", critical=True)
    elif review.get("policy_risk", 0) >= 4:
        r.penalize(10, f"politika riski orta ({review['policy_risk']}/10)")
    if review.get("misleading", 0) >= 6:
        r.penalize(30, f"yanıltıcılık riski yüksek ({review['misleading']}/10)", critical=True)
    elif review.get("misleading", 0) >= 4:
        r.penalize(10, f"yanıltıcılık riski orta ({review['misleading']}/10)")
    if review.get("title_matches_content") is False:
        r.penalize(20, "başlık içerikle uyuşmuyor", critical=True)
    for flag in (review.get("factual_red_flags") or [])[:5]:
        r.penalize(5, f"olgusal şüphe: {flag}")


def check_media(r: Report, media_issues: list[str]) -> None:
    for m in media_issues:
        r.penalize(30, f"medya: {m}", critical=True)
