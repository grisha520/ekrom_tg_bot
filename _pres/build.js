const pptxgen = require("pptxgenjs");
const p = new pptxgen();
p.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
p.author = "max-ekrom-bot";
p.title = "max-ekrom-bot — презентация продукта";

const DARK = "0E2A33", TEAL = "0E7C86", TEALDK = "0B5563";
const MINT = "1FB597", INK = "1E293B", MUTED = "5C7480";
const LIGHT = "F4F8F9", WHITE = "FFFFFF", TINT = "E9F3F3";
const HEAD = "Cambria", BODY = "Calibri";
const W = 13.33;
const sh = () => ({ type: "outer", color: "0B2027", blur: 7, offset: 3, angle: 90, opacity: 0.16 });

function kicker(s, t, x, y, color) {
  s.addText(t, { x, y, w: 8, h: 0.3, margin: 0, fontFace: BODY, fontSize: 12.5, bold: true, color, charSpacing: 3 });
}
function title(s, t, color) {
  s.addText(t, { x: 0.7, y: 0.5, w: 12, h: 0.8, margin: 0, fontFace: HEAD, fontSize: 33, bold: true, color });
}
function card(s, x, y, w, h, fill) {
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: fill }, rectRadius: 0.09, line: { color: "DCE7E8", width: 0.75 }, shadow: sh() });
}
function badge(s, x, y, txt, fill, txtColor) {
  s.addShape(p.shapes.OVAL, { x, y, w: 0.62, h: 0.62, fill: { color: fill } });
  s.addText(txt, { x, y, w: 0.62, h: 0.62, margin: 0, align: "center", valign: "middle", fontFace: HEAD, fontSize: 17, bold: true, color: txtColor });
}

// ---------- Slide 1 — Title ----------
let s = p.addSlide(); s.background = { color: DARK };
s.addShape(p.shapes.OVAL, { x: 10.3, y: -1.6, w: 4.6, h: 4.6, fill: { color: TEAL, transparency: 78 } });
s.addShape(p.shapes.OVAL, { x: 11.8, y: 4.2, w: 3.2, h: 3.2, fill: { color: MINT, transparency: 82 } });
kicker(s, "ПРЕЗЕНТАЦИЯ ПРОДУКТА", 0.85, 1.35, MINT);
s.addText("max-ekrom-bot", { x: 0.8, y: 2.0, w: 11.5, h: 1.1, margin: 0, fontFace: HEAD, fontSize: 56, bold: true, color: WHITE });
s.addText("Автоподбор бытовой техники и коммерческих предложений\nдля менеджеров ekrom.ru", { x: 0.85, y: 3.25, w: 10.8, h: 1.1, margin: 0, fontFace: BODY, fontSize: 21, color: "CFE6E4", lineSpacingMultiple: 1.05 });
s.addText("Сообщение клиента  →  готовый PDF за ~30 секунд", { x: 0.85, y: 4.5, w: 10, h: 0.5, margin: 0, fontFace: BODY, fontSize: 16, italic: true, color: MINT });
const chips = ["Кухня", "Климат", "МБТ", "Уход за одеждой", "Красота"];
let cx = 0.85;
chips.forEach((c) => {
  const w = 0.42 + c.length * 0.135;
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: cx, y: 5.75, w, h: 0.5, fill: { color: TEALDK }, rectRadius: 0.25, line: { color: TEAL, width: 1 } });
  s.addText(c, { x: cx, y: 5.75, w, h: 0.5, margin: 0, align: "center", valign: "middle", fontFace: BODY, fontSize: 13, color: "DCEFEC" });
  cx += w + 0.25;
});

// ---------- Slide 2 — What it does ----------
s = p.addSlide(); s.background = { color: LIGHT };
title(s, "Что делает бот", INK);
s.addText("Менеджер пересылает боту сообщение клиента — бот сам находит технику на ekrom.ru, подбирает по характеристикам и цене и формирует PDF-КП.", { x: 0.7, y: 1.32, w: 12, h: 0.8, margin: 0, fontFace: BODY, fontSize: 15.5, color: MUTED, lineSpacingMultiple: 1.05 });
card(s, 0.7, 2.45, 5.75, 3.9, TINT);
s.addText("Было — вручную", { x: 1.0, y: 2.7, w: 5.2, h: 0.5, margin: 0, fontFace: HEAD, fontSize: 18, bold: true, color: TEALDK });
s.addText([
  { text: "Ищет каждую позицию на сайте", options: { bullet: true, breakLine: true } },
  { text: "Сравнивает цены и характеристики", options: { bullet: true, breakLine: true } },
  { text: "Вручную собирает КП", options: { bullet: true, breakLine: true } },
  { text: "15–30 минут на запрос", options: { bullet: true } },
], { x: 1.0, y: 3.35, w: 5.2, h: 2.7, margin: 0, fontFace: BODY, fontSize: 15, color: INK, paraSpaceAfter: 10 });
card(s, 6.85, 2.45, 5.75, 3.9, WHITE);
s.addText("Стало — с ботом", { x: 7.15, y: 2.7, w: 5.2, h: 0.5, margin: 0, fontFace: HEAD, fontSize: 18, bold: true, color: MINT });
s.addText([
  { text: "Одно сообщение клиента", options: { bullet: true, breakLine: true } },
  { text: "Авто-подбор по всему каталогу", options: { bullet: true, breakLine: true } },
  { text: "Готовый PDF с ценами и «Итого»", options: { bullet: true, breakLine: true } },
  { text: "Около 30 секунд", options: { bullet: true } },
], { x: 7.15, y: 3.35, w: 5.2, h: 2.7, margin: 0, fontFace: BODY, fontSize: 15, color: INK, paraSpaceAfter: 10 });

// ---------- Slide 3 — How it works ----------
s = p.addSlide(); s.background = { color: LIGHT };
title(s, "Как это работает", INK);
const steps = [
  ["1", "Сообщение", "клиента"],
  ["2", "Парсинг", "LLM (Groq)"],
  ["3", "Подбор", "по каталогу ekrom"],
  ["4", "Превью", "менеджеру"],
  ["5", "PDF", "клиенту"],
];
const n = steps.length, cardW = 2.18, gap = (W - 1.4 - n * cardW) / (n - 1);
let x = 0.7;
steps.forEach((st, i) => {
  card(s, x, 2.35, cardW, 2.5, WHITE);
  badge(s, x + cardW / 2 - 0.31, 2.62, st[0], TEAL, WHITE);
  s.addText(st[1], { x, y: 3.45, w: cardW, h: 0.5, margin: 0, align: "center", fontFace: HEAD, fontSize: 16, bold: true, color: INK });
  s.addText(st[2], { x: x + 0.1, y: 3.95, w: cardW - 0.2, h: 0.7, margin: 0, align: "center", fontFace: BODY, fontSize: 12.5, color: MUTED });
  if (i < n - 1) s.addText("›", { x: x + cardW, y: 2.35, w: gap, h: 2.5, margin: 0, align: "center", valign: "middle", fontFace: BODY, fontSize: 30, bold: true, color: MINT });
  x += cardW + gap;
});
s.addText("Полная схема с ответвлениями (умный фильтр Bitrix · обход новой категории · поиск по артикулу) — в Obsidian Canvas.", { x: 0.7, y: 5.4, w: 12, h: 0.6, margin: 0, fontFace: BODY, fontSize: 13.5, italic: true, color: MUTED });

// ---------- Slide 4 — Capabilities ----------
s = p.addSlide(); s.background = { color: LIGHT };
title(s, "Возможности", INK);
const caps = [
  ["Весь каталог техники", "Кухня, климат, МБТ, уход за одеждой, красота — не только встройка"],
  ["Точные модели по артикулу", "Находит конкретный Korting / Bosch / Smeg, даже «под заказ»"],
  ["Ценовые сегменты", "Эконом · средний · премиум — выбор по запросу клиента"],
  ["Характеристики", "Цвет, топливо, размер, тип установки, исключения «не чёрную»"],
  ["Наличие и «под заказ»", "Под заказ предлагается; нет в наличии — отбрасывается"],
  ["Замены с пояснением", "Точного нет → аналог по бренду/сегменту, с понятной пометкой"],
];
const gw = 3.86, gh = 1.95, gx0 = 0.7, gy0 = 1.7, gxg = 0.18, gyg = 0.2;
caps.forEach((c, i) => {
  const col = i % 3, row = Math.floor(i / 3);
  const cardx = gx0 + col * (gw + gxg), cardy = gy0 + row * (gh + gyg);
  card(s, cardx, cardy, gw, gh, WHITE);
  s.addShape(p.shapes.OVAL, { x: cardx + 0.28, y: cardy + 0.3, w: 0.32, h: 0.32, fill: { color: MINT } });
  s.addText(c[0], { x: cardx + 0.75, y: cardy + 0.26, w: gw - 0.95, h: 0.45, margin: 0, valign: "middle", fontFace: HEAD, fontSize: 14.5, bold: true, color: INK });
  s.addText(c[1], { x: cardx + 0.3, y: cardy + 0.82, w: gw - 0.6, h: 0.95, margin: 0, fontFace: BODY, fontSize: 12.5, color: MUTED, lineSpacingMultiple: 1.03 });
});

// ---------- Slide 5 — Channels & access ----------
s = p.addSlide(); s.background = { color: LIGHT };
title(s, "Каналы и доступ", INK);
const rows = [
  ["Telegram-бот для менеджеров", "Основной канал. Доступ только по Telegram user_id; обращения логируются."],
  ["Веб-интерфейс менеджера", "Вставил текст клиента → превью позиций → кнопка «Сформировать PDF»."],
  ["Работает 24/7 на сервере", "Развёрнут на VPS, обновление одним git push, авто-перезапуск."],
];
let ry = 1.75;
rows.forEach((r, i) => {
  card(s, 0.7, ry, 11.93, 1.42, WHITE);
  badge(s, 1.05, ry + 0.4, String(i + 1), TEAL, WHITE);
  s.addText(r[0], { x: 1.95, y: ry + 0.26, w: 10.4, h: 0.45, margin: 0, fontFace: HEAD, fontSize: 17, bold: true, color: INK });
  s.addText(r[1], { x: 1.95, y: ry + 0.74, w: 10.4, h: 0.5, margin: 0, fontFace: BODY, fontSize: 13.5, color: MUTED });
  ry += 1.66;
});

// ---------- Slide 6 — Under the hood ----------
s = p.addSlide(); s.background = { color: LIGHT };
title(s, "Под капотом", INK);
s.addText("Автоматизация реального сайта ekrom + LLM для понимания свободного текста.", { x: 0.7, y: 1.3, w: 12, h: 0.5, margin: 0, fontFace: BODY, fontSize: 15, color: MUTED });
const tech = [
  ["Playwright", "Автоматизация ekrom: поиск, фильтры, корзина, выгрузка PDF"],
  ["Groq LLM", "Разбор сообщения и выбор категории из каталога"],
  ["Детерминированный подбор", "Фильтры характеристик + скоринг по сегменту"],
  ["aiogram + FastAPI", "Telegram-бот и веб-интерфейс менеджера"],
  ["90+ авто-тестов", "Покрыта вся «чистая» логика подбора"],
  ["Деплой на VPS", "Git-clone, systemd, обновление в один git push"],
];
tech.forEach((c, i) => {
  const col = i % 3, row = Math.floor(i / 3);
  const cardx = gx0 + col * (gw + gxg), cardy = gy0 + 0.25 + row * (gh + gyg);
  card(s, cardx, cardy, gw, gh, WHITE);
  s.addText(c[0], { x: cardx + 0.3, y: cardy + 0.26, w: gw - 0.6, h: 0.45, margin: 0, fontFace: HEAD, fontSize: 15, bold: true, color: TEALDK });
  s.addText(c[1], { x: cardx + 0.3, y: cardy + 0.8, w: gw - 0.6, h: 1.0, margin: 0, fontFace: BODY, fontSize: 12.5, color: MUTED, lineSpacingMultiple: 1.03 });
});

// ---------- Slide 7 — Numbers (accent) ----------
s = p.addSlide(); s.background = { color: TEALDK };
s.addText("Скорость и надёжность", { x: 0.7, y: 0.55, w: 12, h: 0.8, margin: 0, fontFace: HEAD, fontSize: 33, bold: true, color: WHITE });
const stats = [
  ["~30 с", "подбор позиции"],
  ["~15 с", "заказ из 6 позиций"],
  ["до 6", "позиций параллельно"],
  ["90+", "авто-тестов"],
];
const sw = 2.86, sgap = (W - 1.4 - stats.length * sw) / (stats.length - 1);
let sx = 0.7;
stats.forEach((st) => {
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: sx, y: 2.2, w: sw, h: 2.7, fill: { color: "0E3A45" }, rectRadius: 0.1, line: { color: TEAL, width: 1 } });
  s.addText(st[0], { x: sx, y: 2.65, w: sw, h: 1.2, margin: 0, align: "center", fontFace: HEAD, fontSize: 50, bold: true, color: MINT });
  s.addText(st[1], { x: sx + 0.15, y: 3.95, w: sw - 0.3, h: 0.7, margin: 0, align: "center", fontFace: BODY, fontSize: 14.5, color: "CFE6E4" });
  sx += sw + sgap;
});
s.addText("Работает 24/7 · стабильно подбирает многопозиционные запросы по всему каталогу", { x: 0.7, y: 5.35, w: 12, h: 0.5, margin: 0, align: "center", fontFace: BODY, fontSize: 14, italic: true, color: "AFD4D0" });

// ---------- Slide 8 — Value ----------
s = p.addSlide(); s.background = { color: DARK };
s.addShape(p.shapes.OVAL, { x: -1.5, y: 4.5, w: 4.2, h: 4.2, fill: { color: TEAL, transparency: 80 } });
kicker(s, "ИТОГ", 0.85, 1.2, MINT);
s.addText("Ценность для бизнеса", { x: 0.8, y: 1.7, w: 11.5, h: 0.9, margin: 0, fontFace: HEAD, fontSize: 38, bold: true, color: WHITE });
const vals = [
  ["Экономит время", "Минуты ручного поиска и сборки КП → секунды"],
  ["Единый формат КП", "Аккуратный PDF с ценами, «Итого» и пометками"],
  ["Быстрый ответ клиенту", "Пока разговор «тёплый» — предложение уже готово"],
];
let vx = 0.8;
const vw = 3.86;
vals.forEach((v, i) => {
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: vx, y: 3.0, w: vw, h: 2.3, fill: { color: "12333D" }, rectRadius: 0.1, line: { color: TEALDK, width: 1 } });
  s.addText(v[0], { x: vx + 0.32, y: 3.3, w: vw - 0.6, h: 0.6, margin: 0, fontFace: HEAD, fontSize: 17, bold: true, color: MINT });
  s.addText(v[1], { x: vx + 0.32, y: 3.95, w: vw - 0.6, h: 1.1, margin: 0, fontFace: BODY, fontSize: 13.5, color: "CFE6E4", lineSpacingMultiple: 1.05 });
  vx += vw + 0.18;
});
s.addText("Схема работы — Obsidian Canvas  ·  Код и история — GitHub", { x: 0.8, y: 5.7, w: 12, h: 0.5, margin: 0, fontFace: BODY, fontSize: 14, italic: true, color: "8FB6B3" });

p.writeFile({ fileName: "max-ekrom-bot — презентация.pptx" }).then((f) => console.log("written:", f));
