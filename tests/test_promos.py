"""Тесты краулера акций и датасета комплектов (Ф1 проекта комплектов).

Разбор HTML/правил проверяем на снимках реальных формулировок ekrom, чтобы
классификация механики и тарифные сетки не «поехали» при правках регулярок.
"""

from __future__ import annotations

from max_ekrom_bot.ekrom.promos import (
    PromoMechanic,
    brand_from_text,
    classify_mechanic,
    parse_campaign,
    parse_campaign_items,
    parse_period,
    parse_tiers,
    parse_top_index,
)
from max_ekrom_bot.ekrom.promo_store import PromoStore

BASE = "https://www.ekrom.ru"

# Реальные формулировки правил (сокращённые) со страниц кампаний ekrom.
KORTING_CASCADE = (
    "1. В акции участвует весь ассортимент техники Korting, кроме мелкой бытовой техники "
    "2. Размер скидки составляет: a. при покупке двух приборов, скидка на наименьший по "
    "стоимости - 25% b. при покупке трех приборов, скидка на наименьший по стоимости - 50% "
    "c. при покупке четырех приборов - 75% c. при покупке пяти приборов -100% "
    "3. В комплекте обязательно наличие духового шкафа."
)
KUP_CASCADE = (
    "При покупке комплекта техники: 2-х предметов техники из разных товарных категорий "
    "скидка -25% на наименьший по стоимости товар 3-х предметов скидка -50% 4-х предметов "
    "скидка -75% 5-и предметов техники скидка -100% на наименьший по стоимости товар"
)
KUP_FLAT = (
    "При покупке комплекта техники Kuppersberg Скидка 5% на комплект из 2 единиц разной "
    "категории; Скидка 7% на комплект из 3 единиц; Скидка 10% на комплект из 4 единиц; "
    "Скидка 12% на комплект из 5 единиц."
)
MAUNFELD_FLAT = (
    "При покупке комплекта из 3-х приборов бытовой техники Maunfeld вы получаете скидку в "
    "размере 8%. При покупке комплекта из 4-х приборов скидку 11%. Из 5-ти приборов 13%. "
    "Из 6-ти приборов 15%."
)


def test_brand_from_text():
    assert brand_from_text("Кухонная вытяжка Korting KHI 6631 RN") == "Korting"
    assert brand_from_text("Midea: каскадные скидки!") == "Midea"
    assert brand_from_text("Franke: 2=3 на все!") is None  # не наш бренд
    assert brand_from_text("") is None


def test_classify_mechanic():
    assert classify_mechanic("Korting: Каскадные скидки", KORTING_CASCADE) == \
        PromoMechanic.CASCADE_CHEAPEST
    assert classify_mechanic("Скидка на комплект техники", KUP_FLAT) == \
        PromoMechanic.FLAT_COMBO
    assert classify_mechanic("Midea: свч в подарок!", "") == PromoMechanic.GIFT
    assert classify_mechanic("Korting: скидка 50% на холодильник", "") == \
        PromoMechanic.SPEC_PRICE
    assert classify_mechanic("Товары с витрины", "") == PromoMechanic.SHOWCASE
    assert classify_mechanic("Korting: Скидки при покупке комплекта МБТ", "") == \
        PromoMechanic.UNKNOWN  # комплект без % и без «наименьший» — итог уточнит менеджер


def test_parse_tiers_cascade_word_counts():
    # Число словом, процент после числа.
    assert parse_tiers(KORTING_CASCADE) == {2: 25.0, 3: 50.0, 4: 75.0, 5: 100.0}


def test_parse_tiers_cascade_hyphen_forms():
    # «2-х», «5-и» — дефис с разными окончаниями.
    assert parse_tiers(KUP_CASCADE) == {2: 25.0, 3: 50.0, 4: 75.0, 5: 100.0}


def test_parse_tiers_percent_first_no_offset():
    # «Скидка P% … из N единиц»: процент ВПЕРЕДИ числа — пары не должны съехать.
    assert parse_tiers(KUP_FLAT) == {2: 5.0, 3: 7.0, 4: 10.0, 5: 12.0}


def test_parse_tiers_flat_starting_at_three():
    # «из 5-ти/6-ти приборов» — окончания «-ти».
    assert parse_tiers(MAUNFELD_FLAT) == {3: 8.0, 4: 11.0, 5: 13.0, 6: 15.0}


def test_parse_tiers_none_for_gift():
    assert parse_tiers("При покупке приборов на сумму 10 000 руб. в подарок чайник") == {}


def test_parse_period_variants():
    assert parse_period("Период проведения акции: 01.06.26 - 31.07.26") == \
        ("2026-06-01", "2026-07-31")
    assert parse_period("до 30.09.2026") == (None, "2026-09-30")
    assert parse_period("от 11.06.2026") == ("2026-06-11", None)
    assert parse_period("нет дат") == (None, None)


# Минимальные, но реалистичные карточки товаров кампании.
_CARD = (
    '<div class="product-card minicard_1 js-product" data-product-id="{pid}">'
    '<a class="product-card__img-box-link" href="{href}" title="картинка {title}"></a>'
    '<div class="product-card__prices">'
    '<span class="product-card__price catprice_{pid} ">{price}</span>'
    '<span class="product-card__price-old catoldprice_{pid}">{old}</span>'
    '</div></div>'
)


def _campaign_html(cards: str, h1: str = "", rules: str = "") -> str:
    return f'<h1>{h1}</h1><div>{rules}</div><div class="products__main">{cards}</div>'


def test_parse_campaign_items():
    cards = (
        _CARD.format(pid="147574", href="/products/dukhovoy_shkaf_korting_okb_370_cmx/",
                     title="Духовой шкаф Korting OKB 370 CMX", price="45 990 ₽", old="")
        + _CARD.format(pid="1000", href="/products/vytyazhka_franke_x/",
                       title="Вытяжка Franke X", price="12 000 ₽", old="15 000 ₽")
    )
    items = parse_campaign_items(cards, BASE)
    assert len(items) == 2
    a, b = items
    assert a.product_id == "147574"
    assert a.url == f"{BASE}/products/dukhovoy_shkaf_korting_okb_370_cmx/"
    assert a.title == "Духовой шкаф Korting OKB 370 CMX"  # префикс «картинка» снят
    assert a.brand == "Korting"
    assert a.price == 45990.0 and a.old_price is None
    assert b.brand is None and b.old_price == 15000.0  # чужой бренд, есть старая цена


def test_parse_top_index():
    top = (
        '<a href="/catalog/aktsii/maunfeld_soberi_svoyu_skidku_/" class="actions__item">'
        '<img src="x.png" alt="Maunfeld: СОБЕРИ СВОЮ СКИДКУ" />'
        '<span>до 31.12.2026</span></a>'
    )
    idx = parse_top_index(top)
    assert idx["maunfeld_soberi_svoyu_skidku_"]["title"] == "Maunfeld: СОБЕРИ СВОЮ СКИДКУ"
    assert idx["maunfeld_soberi_svoyu_skidku_"]["period"] == "до 31.12.2026"


def test_parse_campaign_uses_title_and_period_hint():
    # Страница-баннер без текста: заголовок/срок берём с плитки раздела.
    html = _campaign_html(cards="", h1="", rules="")
    camp = parse_campaign(
        html, "midea_svch_v_podarok", BASE,
        title_hint="Midea: свч в подарок!", period_hint="до 30.09.2026",
    )
    assert camp.brand == "Midea"
    assert camp.mechanic == PromoMechanic.GIFT
    assert camp.title == "Midea: свч в подарок!"
    assert camp.period_to == "2026-09-30"


def test_parse_campaign_full_cascade():
    cards = _CARD.format(pid="1", href="/products/a/", title="Korting A",
                         price="10 000 ₽", old="")
    html = _campaign_html(cards, h1='Korting: "Каскадные скидки"', rules=KORTING_CASCADE)
    camp = parse_campaign(html, "skidki_pri_pokupke_komplekta_korting", BASE)
    assert camp.brand == "Korting"
    assert camp.mechanic == PromoMechanic.CASCADE_CHEAPEST
    assert camp.scope == "all_brand"  # «весь ассортимент»
    assert camp.requires_oven is True
    assert camp.tiers == {2: 25.0, 3: 50.0, 4: 75.0, 5: 100.0}
    assert len(camp.items) == 1


def test_promo_store_roundtrip(tmp_path):
    cards = _CARD.format(pid="1", href="/products/a/", title="Korting A",
                         price="10 000 ₽", old="")
    html = _campaign_html(cards, h1='Korting: "Каскадные скидки"', rules=KORTING_CASCADE)
    camp = parse_campaign(html, "korting_cascade", BASE)

    store = PromoStore(tmp_path / "c.db")
    assert store.replace_all([camp]) == 1
    assert store.count() == 1
    got = store.campaigns_for_brand("Korting")
    assert len(got) == 1
    row = got[0]
    assert row["mechanic"] == PromoMechanic.CASCADE_CHEAPEST
    assert row["tiers"] == {2: 25.0, 3: 50.0, 4: 75.0, 5: 100.0}
    assert row["requires_oven"] is True
    assert store.brand_counts() == {"Korting": 1}


def test_promo_store_replace_all_is_idempotent(tmp_path):
    cards = _CARD.format(pid="1", href="/products/a/", title="A", price="1 ₽", old="")
    camp = parse_campaign(_campaign_html(cards, h1="X"), "s", BASE)
    store = PromoStore(tmp_path / "c.db")
    store.replace_all([camp])
    store.replace_all([camp])  # повторный прогон не дублирует
    assert store.count() == 1
