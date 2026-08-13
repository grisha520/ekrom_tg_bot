"""Тесты клиентского превью комплекта (Ф5)."""

from __future__ import annotations

from max_ekrom_bot.config import Settings
from max_ekrom_bot.matching.combo_pricing import ComboResult
from max_ekrom_bot.matching.kit_builder import BrandKit
from max_ekrom_bot.messenger.client_ux import format_kit, kit_pdf_keyboard, kit_welcome_text
from max_ekrom_bot.models import (
    ApplianceType, CatalogProduct, MatchedItem, ProductSpec, SelectionResult,
)

_S = Settings(LLM_PROVIDER="rule", SHOP_NAME="ekrom")


def _mi(kind, price, appliance):
    return MatchedItem(spec=ProductSpec(appliance=appliance, raw_text=kind),
                       product=CatalogProduct(title=f"{kind} Korting", url="u", price=price))


def _kit(combo, *, matched=None, unmatched=None, requested=2, note="Комплект визуально сочетается."):
    items = list(matched or [])
    for u in (unmatched or []):
        items.append(MatchedItem(spec=ProductSpec(appliance=ApplianceType.UNKNOWN, raw_text=u),
                                 product=None))
    return BrandKit(brand="Korting", selection=SelectionResult(items=items), combo=combo,
                    requested=requested, coverage=len(matched or []),
                    design_consistent=True, design_note=note)


def test_format_kit_cascade_discount():
    combo = ComboResult(mechanic="cascade_cheapest", subtotal=65000, discount_total=6500,
                        final_total=58500, applied=True)
    kit = _kit(combo, matched=[_mi("Духовой шкаф", 40000, ApplianceType.OVEN),
                               _mi("Варочная панель", 25000, ApplianceType.COOKTOP)])
    text = format_kit(kit, _S)
    assert "Комплект Korting" in text
    assert "Выгода по акции" in text and "К оплате" in text
    assert "🎨" in text  # дизайн-пометка


def test_format_kit_gift():
    combo = ComboResult(mechanic="gift", subtotal=60000, discount_total=3990,
                        final_total=60000, applied=True, gift_title="Чайник Korting KWK 0902 G")
    kit = _kit(combo, matched=[_mi("Духовой шкаф", 40000, ApplianceType.OVEN),
                               _mi("Варочная панель", 20000, ApplianceType.COOKTOP)])
    text = format_kit(kit, _S)
    assert "🎁 В подарок" in text and "KWK 0902 G" in text
    assert "60 000" in text  # к оплате не уменьшилось


def test_format_kit_manager_review():
    combo = ComboResult(mechanic="gift", subtotal=60000, discount_total=0,
                        final_total=60000, manager_review=True)
    kit = _kit(combo, matched=[_mi("Духовой шкаф", 40000, ApplianceType.OVEN),
                               _mi("Варочная панель", 20000, ApplianceType.COOKTOP)])
    assert "подтвердит менеджер" in format_kit(kit, _S).lower()


def test_format_kit_partial_coverage():
    combo = ComboResult(mechanic="none", subtotal=40000, discount_total=0, final_total=40000)
    kit = _kit(combo, matched=[_mi("Духовой шкаф", 40000, ApplianceType.OVEN)],
               unmatched=["вытяжка"], requested=2)
    text = format_kit(kit, _S)
    assert "Закрыли 1 из 2" in text
    assert "вытяжка" in text


def test_kit_pdf_keyboard_and_welcome():
    kb = kit_pdf_keyboard("tok123")
    assert kb.inline_keyboard[0][0].callback_data == "kitpdf:tok123"
    assert "комплект" in kit_welcome_text(_S).lower()


def test_kit_shows_campaign_title_and_deadline():
    # Клиент должен видеть, ПО КАКОЙ акции посчитано и до какого числа она жива.
    from max_ekrom_bot.messenger.client_ux import _promo_line

    class _C:
        campaign_title = "Korting: Формула выгоды"
        campaign_period_to = "2026-09-30"
        applied = True
        manager_review = False

    line = _promo_line(_C())
    assert "Korting: Формула выгоды" in line
    assert "до 30.09.2026" in line


def test_kit_promo_line_hidden_without_benefit():
    # Акция не сработала (Lex/нет тарифа) — название кампании не показываем,
    # иначе клиент решит, что скидку потеряли.
    from max_ekrom_bot.messenger.client_ux import _promo_line

    class _C:
        campaign_title = "Korting: Каскадные скидки"
        campaign_period_to = "2026-09-30"
        applied = False
        manager_review = False

    assert _promo_line(_C()) == ""


def test_kit_promo_line_without_deadline():
    # Акция с открытым сроком («до уведомления») — только название, без даты.
    from max_ekrom_bot.messenger.client_ux import _promo_line

    class _C:
        campaign_title = "Midea: каскадные скидки!"
        campaign_period_to = None
        applied = True
        manager_review = False

    line = _promo_line(_C())
    assert "Midea" in line and "действует до" not in line
