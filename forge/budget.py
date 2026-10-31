"""Maliyet defteri ve bütçe koruması.

Varsayılan bütçe 0 $'dır: yalnızca ücretsiz sağlayıcılar kullanılır. Ücretli bir sağlayıcı
eklenirse `guard()` önce bütçeyi kontrol eder; %80'de uyarı, %100'de durdurur.
Kullanıcının onayı olmadan ücretli abonelik açılmaz.
"""
from __future__ import annotations

from . import config
from .db import DB


class BudgetExceeded(Exception):
    pass


def guard(db: DB, est_usd: float, *, item_id: int | None = None, category: str = "misc") -> None:
    """Ücretli bir işten önce çağır. est_usd == 0 ise her zaman geçer."""
    if est_usd <= 0:
        return
    budget = config.budget_usd(db.channel)
    spent = db.month_cost()
    if budget <= 0 or spent + est_usd > budget:
        db.log("error", f"Bütçe aşılacak: harcanan ${spent:.2f} + ${est_usd:.2f} > ${budget:.2f} ({category})")
        raise BudgetExceeded(f"{db.channel}: aylık bütçe ${budget:.2f} yetersiz")
    if spent + est_usd >= 0.8 * budget:
        db.log("warn", f"Bütçenin %80'i aşıldı: ${spent + est_usd:.2f} / ${budget:.2f}")


def record(db: DB, category: str, usd: float, item_id: int | None = None, note: str = "") -> None:
    db.add_cost(category, usd, item_id, note)
