"""Тесты краулера акций и датасета комплектов (Ф1 проекта комплектов).

Разбор HTML/правил проверяем на снимках реальных формулировок ekrom, чтобы
классификация механики и тарифные сетки не «поехали» при правках регулярок.
"""

from __future__ import annotations

from max_ekrom_bot.ekrom.promos import (
    GiftRule,
    PromoMechanic,
    brand_from_text,
    classify_mechanic,
    parse_campaign,
    parse_campaign_items,
    parse_exclusions,
    parse_gift,
    parse_period,
    parse_spec_price,
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


GIFT_SUM = (
    "3. Подарки предоставляются: a) при покупке приборов на сумму 10 000 руб. – в подарок "
    "предоставляется стеклянный чайник KWK 0902 G ; b) при покупке приборов на сумму 25 000 "
    "руб. – в подарок предоставляется погружной блендер KHB 0315 W Tulip ; с) при покупке "
    "приборов на сумму 50 000 руб. – в подарок предоставляется кофеварка KCM 1005 T Arte ."
)
GIFT_COUNT = (
    "Механика: Встраиваемые посудомоечные машины MID45S100I или 60S100I в подарок при покупке "
    "4-х приборов из разных товарных групп техники Midea. Духовой шкаф должен быть в комплекте."
)
GIFT_CHOICE = (
    "Подарок или специальная цена на выбор: При покупке комплекта из 3-х предметов в подарок "
    "на выбор предоставляется КА-00033464 Посудомоечная машина MAUNFELD MLP45530M."
)


def test_parse_gift_sum_thresholds():
    g = parse_gift(GIFT_SUM)
    assert isinstance(g, GiftRule)
    assert g.condition == "sum"
    assert g.options == {10000: "KWK 0902", 25000: "KHB 0315", 50000: "KCM 1005"}


def test_parse_gift_count_threshold():
    g = parse_gift(GIFT_COUNT)
    assert g.condition == "count"
    # Модель целиком: обрезка до «MID45» матчила бы ЛЮБУЮ ПММ MID45* как подарок.
    assert g.options == {4: "MID45S100I"}


def test_parse_gift_choice_is_not_digitized():
    # «на выбор»/«спец цена» — слишком вариативно, оставляем менеджеру.
    assert parse_gift(GIFT_CHOICE) is None


def test_parse_gift_empty_banner():
    assert parse_gift("") is None
    assert parse_gift("Korting раздаёт подарки!") is None  # баннер без условий


def test_parse_campaign_sets_gift_only_for_gift_mechanic():
    html = _campaign_html("", h1="Korting: Покупка это подарок!", rules=GIFT_SUM)
    camp = parse_campaign(html, "korting_mbt_v_podarok", BASE)
    assert camp.mechanic == PromoMechanic.GIFT
    assert camp.gift and camp.gift.condition == "sum"


# Реальные условия «спеццена/50% на холодильник» (стрип с сайта, с разрывом «с кидка»).
KUP_SPEC_RULES = (
    "Период: с 01.07.2026 по 30.09.2026. При покупке комплекта техники Kuppersberg: "
    "Из 3-х предметов → 4-й предмет (на выбор) по цене Встраиваемый холодильник RBN 1761 "
    "за 40 000 руб. Встраиваемый холодильник RBN 1965 за 40 000 руб Из 4-х предметов → "
    "5-й предмет (на выбор) по цене Встраиваемый холодильник RBN 1761 за 27 000 руб. "
    "Встраиваемый холодильник RBN 1965 за 27 000 руб. Условия: В комплекте обязательно "
    "должен присутствовать Духовой шкаф. Варочные панели Домино, Измельчители, Соло СВЧ "
    "в акции не участвуют. Отдельностоящие и полувстраиваемые холодильники не участвуют."
)
KOR_50_RULES = (
    'Скидка 50% на холодильник 1. Период проведения акции: 1.06.26 – 31.07.26. '
    '2. В акции участвует линейка "Эксклюзив"(отмечены шильдиком "эксклюзив") и основной '
    'ассортимент техники Körting, кроме малой бытовой техники и аксессуаров. 3. При покупке '
    'встраиваемого духового шкафа из линейки "Эксклюзив" и еще одного прибора (из любой '
    'линейки) предоставляется с кидка 50% на встраиваемый холодильник KSI 19547 CFNFZ.'
)


def test_parse_spec_price_kuppersberg_tiers():
    spec = parse_spec_price(KUP_SPEC_RULES)
    assert spec is not None
    assert spec.models == ["RBN 1761", "RBN 1965"]
    assert spec.tiers == {3: 40000.0, 4: 27000.0}
    assert spec.pct is None and spec.line_label is None


def test_parse_spec_price_korting_pct_with_line():
    spec = parse_spec_price(KOR_50_RULES)
    assert spec is not None
    assert spec.pct == 50.0                      # терпим разрыв «с кидка»
    assert spec.models == ["KSI 19547"]
    assert spec.line_label and "ксклюзив" in spec.line_label


def test_parse_spec_price_none_for_other_rules():
    assert parse_spec_price(KORTING_CASCADE) is None
    assert parse_spec_price(GIFT_SUM) is None
    assert parse_spec_price("") is None


def test_parse_exclusions():
    assert parse_exclusions(KUP_SPEC_RULES) == [
        "домино", "отдельностоящая", "соло-свч", "измельчитель",
    ]
    assert parse_exclusions("Не участвуют: Соло СВЧ, Измельчители, Соло Кофемашины, "
                            "вся отдельностоящая техника") == [
        "отдельностоящая", "соло-свч", "измельчитель", "соло-кофемашина",
    ]
    # Упоминание категории БЕЗ «не участвуют» рядом — не исключение.
    assert parse_exclusions("Варочные панели Домино 1 Духовые шкафы 1") == []


def test_parse_campaign_spec_price_beats_gift_classifier():
    # В тексте спеццены есть слова «подарок»/«скидка» — но механика должна быть spec_price.
    html = (
        '<div class="title title--banner"><h2>Kuppersberg: Холодильник по спец цене!</h2></div>'
        f'<div class="action-descr__inner"><p>{KUP_SPEC_RULES} Возможна замена подарка '
        'на более функциональную модель.</p></div>'
    )
    camp = parse_campaign(html, "kuppersberg_kholodilnik_po_spets_tsene", BASE)
    assert camp.mechanic == PromoMechanic.SPEC_PRICE
    assert camp.title == "Kuppersberg: Холодильник по спец цене!"
    assert camp.spec and camp.spec.tiers == {3: 40000.0, 4: 27000.0}
    assert camp.requires_oven is True            # «должен присутствовать Духовой шкаф»
    assert "домино" in camp.exclusions


def test_extract_rules_from_action_descr():
    # Условия в action-descr__inner, а не под <h1> (страницы акций ekrom).
    html = (
        '<h1>Каталог</h1><div class="title title--banner"><h2>Тест: акция</h2></div>'
        '<div class="action-descr__inner"><p><b>Период: до 31.12.2026.</b></p>'
        '<p>При покупке двух приборов скидка 25%</p></div>'
        '<div class="products__main">…</div>'
    )
    camp = parse_campaign(html, "test_aktsiya", BASE)
    assert camp.title == "Тест: акция"
    assert "Период: до 31.12.2026" in camp.rules_text
    assert "При покупке двух приборов" in camp.rules_text


def test_promo_store_spec_roundtrip(tmp_path):
    from datetime import date

    html = (
        '<div class="title title--banner"><h2>Kuppersberg: Холодильник по спец цене!</h2></div>'
        f'<div class="action-descr__inner"><p>{KUP_SPEC_RULES}</p></div>'
    )
    camp = parse_campaign(html, "kup_spec", BASE)
    store = PromoStore(tmp_path / "c.db")
    store.replace_all([camp])
    # Дата внутри срока акции из правил (01.07–30.09.2026): хранилище отсекает
    # истёкшие кампании по «сегодня», и без фиксированной даты тест начал
    # падать сам собой с 01.10.2026.
    row = store.campaigns_for_brand("Kuppersberg", today=date(2026, 8, 1))[0]
    assert row["mechanic"] == "spec_price"
    assert row["spec"]["tiers"] == {3: 40000.0, 4: 27000.0}
    assert row["spec"]["models"] == ["RBN 1761", "RBN 1965"]
    assert row["exclusions"] == ["домино", "отдельностоящая", "соло-свч", "измельчитель"]


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


def test_promo_store_gift_roundtrip(tmp_path):
    # Оцифрованное условие подарка сохраняется и читается обратно как dict.
    html = _campaign_html("", h1="Korting: Покупка это подарок!", rules=GIFT_SUM)
    camp = parse_campaign(html, "korting_mbt_v_podarok", BASE)
    store = PromoStore(tmp_path / "c.db")
    store.replace_all([camp])
    row = store.campaigns_for_brand("Korting")[0]
    assert row["gift"] == {"condition": "sum",
                           "options": {10000: "KWK 0902", 25000: "KHB 0315", 50000: "KCM 1005"}}


def test_promo_store_gift_none_for_non_gift(tmp_path):
    html = _campaign_html("", h1='Korting: "Каскадные скидки"', rules=KORTING_CASCADE)
    camp = parse_campaign(html, "korting_cascade", BASE)
    store = PromoStore(tmp_path / "c.db")
    store.replace_all([camp])
    assert store.campaigns_for_brand("Korting")[0]["gift"] is None


def test_promo_store_replace_all_is_idempotent(tmp_path):
    cards = _CARD.format(pid="1", href="/products/a/", title="A", price="1 ₽", old="")
    camp = parse_campaign(_campaign_html(cards, h1="X"), "s", BASE)
    store = PromoStore(tmp_path / "c.db")
    store.replace_all([camp])
    store.replace_all([camp])  # повторный прогон не дублирует
    assert store.count() == 1


def test_scope_detection_three_way():
    # «весь ассортимент» → all_brand; явное «участвуют только» → listed;
    # без формулировки → unspecified (витрина ≠ состав акции, список не навязываем).
    camp_all = parse_campaign(_campaign_html("", rules=""), "s", BASE,
                              title_hint="Korting: акция")
    assert camp_all.scope == "unspecified"
    html = ('<div class="title title--banner"><h2>Korting: акция</h2></div>'
            '<div class="action-descr__inner"><p>В акции участвуют только модели '
            'из списка ниже. Скидка 5% на комплект из 2 единиц; '
            'Скидка 7% на комплект из 3 единиц.</p></div>')
    assert parse_campaign(html, "s", BASE).scope == "listed"
    html_all = ('<div class="title title--banner"><h2>Korting: акция</h2></div>'
                f'<div class="action-descr__inner"><p>{KORTING_CASCADE}</p></div>')
    assert parse_campaign(html_all, "s", BASE).scope == "all_brand"


def _store_with(tmp_path, **overrides):
    """PromoStore с одной Korting-кампанией; поля кампании правим через overrides."""
    html = _campaign_html("", h1='Korting: "Каскадные скидки"', rules=KORTING_CASCADE)
    camp = parse_campaign(html, "korting_cascade", BASE)
    for k, v in overrides.items():
        setattr(camp, k, v)
    store = PromoStore(tmp_path / "c.db")
    store.replace_all([camp])
    return store


def test_campaigns_for_brand_skips_expired(tmp_path):
    from datetime import date

    store = _store_with(tmp_path, period_to="2026-08-31")
    assert store.campaigns_for_brand("Korting", today=date(2026, 8, 13)) != []
    # В день окончания акция ещё действует, после — нет.
    assert store.campaigns_for_brand("Korting", today=date(2026, 8, 31)) != []
    assert store.campaigns_for_brand("Korting", today=date(2026, 9, 1)) == []


def test_campaigns_for_brand_skips_not_started(tmp_path):
    from datetime import date

    store = _store_with(tmp_path, period_from="2026-09-01", period_to=None)
    assert store.campaigns_for_brand("Korting", today=date(2026, 8, 13)) == []
    assert store.campaigns_for_brand("Korting", today=date(2026, 9, 1)) != []


def test_campaigns_for_brand_keeps_undated(tmp_path):
    # Кампании без распознанных дат считаем действующими (срок был жив при крауле).
    store = _store_with(tmp_path, period_from=None, period_to=None)
    assert store.campaigns_for_brand("Korting") != []


def test_campaigns_for_brand_listed_urls(tmp_path):
    # Для scope=listed отдаём ссылки витринных участников (проверка списка в движке);
    # для остальных охватов список пуст (не навязываем витрину как состав акции).
    cards = _CARD.format(pid="1", href="/products/a/", title="Korting A",
                         price="10 000 ₽", old="")
    html = _campaign_html(cards, h1='Korting: "Каскадные скидки"', rules=KORTING_CASCADE)
    camp = parse_campaign(html, "korting_cascade", BASE)

    camp.scope = "listed"
    store = PromoStore(tmp_path / "a.db")
    store.replace_all([camp])
    row = store.campaigns_for_brand("Korting")[0]
    assert row["listed_urls"] == [f"{BASE}/products/a/"]

    camp.scope = "all_brand"
    store2 = PromoStore(tmp_path / "b.db")
    store2.replace_all([camp])
    assert store2.campaigns_for_brand("Korting")[0]["listed_urls"] == []


def test_promo_store_migrates_missing_gift_column(tmp_path):
    # БД прошлой версии без gift_json — PromoStore должен добить столбец на месте.
    import sqlite3

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as c:
        c.execute(
            "CREATE TABLE promo_campaigns (slug TEXT PRIMARY KEY, url TEXT, title TEXT, "
            "brand TEXT, mechanic TEXT, period_from TEXT, period_to TEXT, scope TEXT, "
            "requires_oven INTEGER, tiers TEXT, rules_text TEXT, updated_at REAL)"
        )
    html = _campaign_html("", h1="Korting: Покупка это подарок!", rules=GIFT_SUM)
    camp = parse_campaign(html, "korting_mbt_v_podarok", BASE)
    store = PromoStore(db)  # миграция при инициализации
    store.replace_all([camp])
    assert store.campaigns_for_brand("Korting")[0]["gift"]["condition"] == "sum"


# Реальный текст правил «Korting: Формула выгоды» (снят с сайта 13.08.2026) —
# новая подарочная механика-«лестница»: модель подарка стоит ПОСЛЕ «в подарок»,
# порогов несколько, даты периода с пробелами внутри.
KORTING_FORMULA = (
    "Формула выгоды 1. Период проведения акции: 1.08. 2026 – 30. 09. 2026. "
    "2. В акции участвует весь ассортимент крупной бытовой техники бренда KÖRTING "
    "(отдельностоящая и встраиваемая). В акции не участвуют: отдельностоящая "
    "микроволновая печь, двухконфорочные варочные панели и малая бытовая техника. "
    "3. Подарки за покупку комплекта техники KÖRTING: a. при покупке 2 приборов – "
    "в подарок предоставляется полновстраиваемая вытяжка KHI 6393 N; b. при покупке "
    "3 приборов – в подарок предоставляется электрический духовой шкаф OKB 3810 FGN; "
    "c. при покупке 4 приборов – в подарок предоставляется посудомоечная машина "
    "KDI 45140 или KDI 60110. 4. Все приборы в комплекте должны быть из разных "
    "товарных категорий актуального ассортимента KÖRTING."
)


def test_parse_gift_ladder_model_after_gift_word():
    g = parse_gift(KORTING_FORMULA)
    assert g is not None and g.condition == "count"
    assert g.options == {2: "KHI 6393", 3: "OKB 3810", 4: "KDI 45140"}


def test_parse_period_with_spaces_inside_dates():
    assert parse_period("Период проведения акции: 1.08. 2026 – 30. 09. 2026.") == \
        ("2026-08-01", "2026-09-30")


def test_parse_exclusions_formula_vygody():
    # «двухконфорочные» → домино; «отдельностоящая микроволновая» → соло-СВЧ,
    # НЕ бланкетное «отдельностоящая» (в акции участвует отдельностоящая техника);
    # «малая бытовая техника» (именительный падеж) → мбт.
    exc = parse_exclusions(KORTING_FORMULA)
    assert "домино" in exc
    assert "соло-свч" in exc
    assert "мбт" in exc
    assert "отдельностоящая" not in exc


def test_parse_campaign_formula_vygody_end_to_end():
    html = ('<div class="title title--banner"><h2>Korting: Формула выгоды</h2></div>'
            f'<div class="action-descr__inner"><p>{KORTING_FORMULA}</p></div>')
    camp = parse_campaign(html, "korting_formula_vygody", BASE)
    assert camp.brand == "Korting"
    assert camp.mechanic == PromoMechanic.GIFT
    assert camp.scope == "all_brand"
    assert camp.period_from == "2026-08-01" and camp.period_to == "2026-09-30"
    assert camp.gift and camp.gift.options[4] == "KDI 45140"


def test_parse_period_open_ended_is_start_not_end():
    # «с <дата> - до уведомления» — это ДАТА НАЧАЛА с открытым концом. Слово «до»
    # относится к «уведомлению»: раньше такую акцию Midea считали истёкшей.
    assert parse_period("Срок действия акции: с 01.01.2026 - до уведомления") == \
        ("2026-01-01", None)
    assert parse_period("Срок проведения акции: с 01.04.2026 - до уведомления") == \
        ("2026-04-01", None)


def test_parse_period_prepositions():
    assert parse_period("Период: с 01.07.2026 по 30.09.2026") == ("2026-07-01", "2026-09-30")
    # Начало без предлога, конец с «по» (реальная запись Kuppersberg).
    assert parse_period("Период проведения: 14 .01.2026 по 30.09.2026.") == \
        ("2026-01-14", "2026-09-30")


def test_parse_campaign_prefers_tile_period_over_rules():
    # Сайт продлил акцию (плитка «до 31.08.2026»), а в тексте правил остался
    # старый срок 05.08.2026 — верим плитке, иначе живая акция «истечёт».
    rules = "Срок проведения акции: 07.05.26 – 05.08.2026 Механика акции: подарок."
    html = ('<div class="title title--banner"><h2>Maunfeld: подарок за новинку!</h2></div>'
            f'<div class="action-descr__inner"><p>{rules}</p></div>')
    camp = parse_campaign(html, "maunfeld_podarok_za_novinku", BASE,
                          period_hint="до 31.08.2026")
    assert camp.period_to == "2026-08-31"
