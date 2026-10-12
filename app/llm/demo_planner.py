"""Offline stand-in for Gemini's reasoning so the demo runs with no API key.

It chooses tools from keywords, waits for their results, then writes a reply using only numbers copied from those
results — the same contract the system prompt imposes on Gemini, so the grounding check applies unchanged.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Any

from app.llm.gateway import LLMResponse, ToolCall

AREAS = ("KL City Centre", "Bangsar South", "Mont Kiara", "Petaling Jaya", "Cyberjaya", "Cheras", "Kajang",
         "Setapak", "Puchong", "Shah Alam", "Semenyih", "Rawang")
_PRICE = re.compile(r"(?:RM\s?(\d[\d,]*(?:\.\d+)?)\s?(k|mil|juta|ribu)?)|(?:\b(\d{2,4})\s?(k)\b)", re.IGNORECASE)
_MULT = {"k": 1000, "ribu": 1000, "mil": 1_000_000, "juta": 1_000_000}
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "satu": 1, "dua": 2, "tiga": 3, "empat": 4, "lima": 5, "enam": 6, "tujuh": 7, "lapan": 8,
    "sembilan": 9, "sepuluh": 10, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
    "八": 8, "九": 9, "十": 10,
}
_QTY = r"(\d+|" + "|".join(_NUMBER_WORDS) + r")"
_YEARS = re.compile(_QTY + r"\s*(?:years?|tahun|年)", re.IGNORECASE)
_MONTHS = re.compile(_QTY + r"\s*(?:months?|bulan|个月)", re.IGNORECASE)
_MALAY = {"saya", "boleh", "berapa", "rumah", "mampu", "kalau", "anak", "beli", "dekat", "apa", "untuk",
          "kenapa", "mengapa", "harga", "keluarkan", "nak", "ke", "tak", "dengan", "jika"}

INTENTS: dict[str, tuple[str, ...]] = {
    "afford": ("afford", "can i buy", "mampu", "boleh beli", "买得起", "负担"),
    "why": ("why", "lower than", "kenapa", "mengapa", "为什么"),
    "epf": ("epf", "kwsp", "akaun sejahtera", "公积金"),
    "first_child": ("child", "baby", "anak", "孩子", "宝宝"),
    "marriage": ("marry", "married", "wedding", "kahwin", "结婚"),
    "job_loss": ("lose my job", "job loss", "laid off", "hilang kerja", "失业"),
    "rate_rise": ("rate rise", "rates rise", "interest rate", "opr", "kadar faedah", "利率"),
    "car_replacement": ("new car", "replace my car", "kereta baru", "换车"),
    "path": ("what would i need", "reach", "get to", "capai", "达到", "path"),
    "stock": ("show me", "listing", "homes", "properties", "senarai rumah", "rumah di", "房源", "附近"),
    "regulation": ("stamp duty", "duti setem", "margin of finance", "tenure", "sbr", "scheme", "skim",
                   "legal fee", "crypto", "印花税"),
}
SCENARIOS = ("first_child", "marriage", "job_loss", "rate_rise", "car_replacement")

T: dict[str, dict[str, str]] = {
    "en": {
        "not_confirmed": "Before I can work out any numbers I need your latest payslip and your EPF statement. "
                         "Upload them and I'll show you exactly what I read so you can check it first.",
        "no": "No. RM{price} is above what your figures support. Your computed maximum is RM{max}, because {reason}.",
        "yes": "Yes. RM{price} is within your computed maximum of RM{max}.",
        "max": "Your computed maximum property price is RM{max}, and {reason}.",
        "loan": "That is a loan of up to RM{loan} at {rate}% over {tenure} years, with a monthly instalment of RM{inst}.",
        "dsr": "Your debt service ratio today is {dsr}% against a {limit}% limit for your income band.",
        "upfront": "At RM{price} you would need RM{total} in cash upfront; after your EPF Akaun Sejahtera the "
                   "shortfall is RM{short}.",
        "epf": "You can put RM{eligible} from Akaun Sejahtera towards the purchase. {note}",
        "why": "Here is what drives the gap:",
        "cite": "• {title}, “{section}”: {snippet}",
        "scenario": "{label} at RM{price}: {status}.",
        "breaks": " Your cash runs out in month {month}, with a shortfall of RM{short} a month.",
        "at_risk": " Your savings stay positive but fall below a safe buffer.",
        "safe": " Your cashflow holds through the projection.",
        "path": "To reach RM{target}:",
        "CLEAR_COMMITMENT": "• Month {month}: settle your {kind} (RM{amount}), freeing RM{monthly} a month.",
        "COMMITMENT_ENDS": "• Month {month}: your {kind} ends, freeing RM{monthly} a month.",
        "SAVE_UPFRONT_CASH": "• Save RM{monthly} a month until you hold RM{amount} for upfront costs.",
        "INCREASE_NET_INCOME": "• Net income would need to reach RM{amount} a month.",
        "REDUCE_TARGET_PRICE": "• Within the plan horizon the most you could reach is RM{amount}.",
        "eligible": "On these assumptions you could be eligible from month {month}.",
        "not_eligible": "On these assumptions you don't reach that price within the plan horizon.",
        "stock": "Homes at or below your computed RM{max}{area}:",
        "listing": "• {title}, {area}: RM{price} ({bedrooms} bed, {status})",
        "no_stock": "I found no listings at or below RM{max}{area}.",
        "kb_miss": "My knowledge base doesn't cover that, so I won't guess. Please check with the relevant "
                   "authority (Bank Negara Malaysia, KWSP or LHDN).",
        "unverified": "Note: this uses figures you told me ({fields}) rather than ones read from a document, so "
                      "treat the result as unverified.",
        "failed": "I couldn't compute that part, so I won't put a number on it.",
        "help": "I can check what you can afford, explain the rules behind it, stress-test life events and plan a "
                "route to a target price. What would you like to look at?",
        "in_area": " in {area}",
        "DSR": "your debt service ratio is the binding limit",
        "TENURE_AGE": "your age caps the loan tenure",
        "CASH_UPFRONT": "the cash needed upfront is the binding limit",
        "MARGIN_OF_FINANCE": "the lower margin of finance raises the cash you need",
        "first_child": "A first child", "marriage": "Marriage", "job_loss": "A job loss",
        "rate_rise": "A rate rise", "car_replacement": "Replacing your car",
        "SAFE": "SAFE", "AT_RISK": "AT RISK", "BREAKS": "BREAKS",
        "completed": "completed", "under_construction": "under construction",
    },
    "ms": {
        "not_confirmed": "Sebelum saya boleh mengira apa-apa angka, saya perlukan slip gaji terkini dan penyata KWSP "
                         "anda. Muat naik dahulu dan saya akan tunjukkan apa yang saya baca untuk anda semak.",
        "no": "Tidak. RM{price} melebihi apa yang angka anda mampu. Harga maksimum yang dikira ialah RM{max}, "
              "kerana {reason}.",
        "yes": "Ya. RM{price} berada dalam had maksimum dikira anda iaitu RM{max}.",
        "max": "Harga hartanah maksimum yang dikira untuk anda ialah RM{max}, dan {reason}.",
        "loan": "Ini bermakna pinjaman sehingga RM{loan} pada {rate}% selama {tenure} tahun, dengan ansuran "
                "bulanan RM{inst}.",
        "dsr": "Nisbah khidmat hutang (DSR) anda hari ini ialah {dsr}% berbanding had {limit}% untuk julat "
               "pendapatan anda.",
        "upfront": "Pada RM{price} anda perlukan RM{total} tunai pendahuluan; selepas Akaun Sejahtera KWSP, "
                   "kekurangannya ialah RM{short}.",
        "epf": "Anda boleh gunakan RM{eligible} daripada Akaun Sejahtera untuk pembelian ini. {note}",
        "why": "Inilah punca jurangnya:",
        "cite": "• {title}, “{section}”: {snippet}",
        "scenario": "{label} pada RM{price}: {status}.",
        "breaks": " Wang tunai anda habis pada bulan {month}, dengan kekurangan RM{short} sebulan.",
        "at_risk": " Simpanan anda kekal positif tetapi jatuh di bawah penampan selamat.",
        "safe": " Aliran tunai anda kekal kukuh sepanjang unjuran.",
        "path": "Untuk mencapai RM{target}:",
        "CLEAR_COMMITMENT": "• Bulan {month}: langsaikan {kind} (RM{amount}), menjimatkan RM{monthly} sebulan.",
        "COMMITMENT_ENDS": "• Bulan {month}: {kind} anda tamat, menjimatkan RM{monthly} sebulan.",
        "SAVE_UPFRONT_CASH": "• Simpan RM{monthly} sebulan sehingga anda ada RM{amount} untuk kos pendahuluan.",
        "INCREASE_NET_INCOME": "• Pendapatan bersih perlu mencapai RM{amount} sebulan.",
        "REDUCE_TARGET_PRICE": "• Dalam tempoh pelan, harga tertinggi yang boleh dicapai ialah RM{amount}.",
        "eligible": "Berdasarkan andaian ini anda mungkin layak mulai bulan {month}.",
        "not_eligible": "Berdasarkan andaian ini anda tidak mencapai harga itu dalam tempoh pelan.",
        "stock": "Rumah pada atau di bawah RM{max} yang dikira{area}:",
        "listing": "• {title}, {area}: RM{price} ({bedrooms} bilik, {status})",
        "no_stock": "Tiada senarai pada atau di bawah RM{max}{area}.",
        "kb_miss": "Pangkalan pengetahuan saya tidak meliputi perkara itu, jadi saya tidak akan meneka. Sila semak "
                   "dengan pihak berkuasa berkaitan (Bank Negara Malaysia, KWSP atau LHDN).",
        "unverified": "Nota: ini menggunakan angka yang anda nyatakan ({fields}), bukan yang dibaca daripada "
                      "dokumen, jadi anggap keputusan ini belum disahkan.",
        "failed": "Saya tidak dapat mengira bahagian itu, jadi saya tidak akan memberi angka.",
        "help": "Saya boleh semak kemampuan anda, terangkan peraturannya, uji tekanan peristiwa hidup dan rancang "
                "laluan ke harga sasaran. Apa yang anda mahu lihat?",
        "in_area": " di {area}",
        "DSR": "nisbah khidmat hutang (DSR) anda ialah had utama",
        "TENURE_AGE": "umur anda menghadkan tempoh pinjaman",
        "CASH_UPFRONT": "wang tunai pendahuluan yang diperlukan ialah had utama",
        "MARGIN_OF_FINANCE": "margin pembiayaan yang lebih rendah meningkatkan tunai yang diperlukan",
        "first_child": "Anak pertama", "marriage": "Perkahwinan", "job_loss": "Kehilangan kerja",
        "rate_rise": "Kenaikan kadar faedah", "car_replacement": "Menukar kereta",
        "SAFE": "SELAMAT", "AT_RISK": "BERISIKO", "BREAKS": "GAGAL",
        "completed": "siap", "under_construction": "dalam pembinaan",
    },
    "zh": {
        "not_confirmed": "在计算任何数字之前，我需要您最新的工资单和公积金（KWSP）结单。上传后我会先给您看我读到的内容，请您核对。",
        "no": "不行。RM{price} 超出了您的财务条件。计算出的最高房价为 RM{max}，原因是{reason}。",
        "yes": "可以。RM{price} 在您计算出的最高房价 RM{max} 之内。",
        "max": "您计算出的最高房价为 RM{max}，{reason}。",
        "loan": "即贷款最高 RM{loan}，利率 {rate}%，年期 {tenure} 年，每月供款 RM{inst}。",
        "dsr": "您目前的偿债比率（DSR）为 {dsr}%，您所在收入段的上限为 {limit}%。",
        "upfront": "以 RM{price} 计算，您需要 RM{total} 的首期现金；扣除公积金 Akaun Sejahtera 后，缺口为 RM{short}。",
        "epf": "您可以从 Akaun Sejahtera 提取 RM{eligible} 用于购房。{note}",
        "why": "差距的原因如下：",
        "cite": "• {title}，“{section}”：{snippet}",
        "scenario": "{label}（房价 RM{price}）：{status}。",
        "breaks": " 您的现金将在第 {month} 个月耗尽，每月缺口 RM{short}。",
        "at_risk": " 您的储蓄仍为正数，但低于安全缓冲。",
        "safe": " 在整个预测期内现金流保持稳定。",
        "path": "要达到 RM{target}：",
        "CLEAR_COMMITMENT": "• 第 {month} 个月：还清您的{kind}（RM{amount}），每月释放 RM{monthly}。",
        "COMMITMENT_ENDS": "• 第 {month} 个月：您的{kind}结束，每月释放 RM{monthly}。",
        "SAVE_UPFRONT_CASH": "• 每月储蓄 RM{monthly}，直到备好 RM{amount} 的首期费用。",
        "INCREASE_NET_INCOME": "• 每月净收入需要达到 RM{amount}。",
        "REDUCE_TARGET_PRICE": "• 在规划期内，您最多可以达到 RM{amount}。",
        "eligible": "按这些假设，您可能从第 {month} 个月起符合条件。",
        "not_eligible": "按这些假设，您在规划期内达不到该价格。",
        "stock": "不超过您计算出的 RM{max} 的房源{area}：",
        "listing": "• {title}，{area}：RM{price}（{bedrooms} 房，{status}）",
        "no_stock": "没有找到不超过 RM{max} 的房源{area}。",
        "kb_miss": "我的知识库没有涵盖这一点，所以我不会猜测。请向相关机构（马来西亚国家银行、公积金局或内陆税收局）核实。",
        "unverified": "注意：这里使用了您告诉我的数字（{fields}），并非从文件中读取，因此结果未经核实。",
        "failed": "我无法计算这一部分，所以不会给出数字。",
        "help": "我可以评估您的购房能力、解释相关规定、对人生事件做压力测试，并规划达到目标价格的路径。您想先看哪一项？",
        "in_area": "（{area}）",
        "DSR": "偿债比率（DSR）是限制因素",
        "TENURE_AGE": "年龄限制了贷款年期",
        "CASH_UPFRONT": "首期现金需求是限制因素",
        "MARGIN_OF_FINANCE": "较低的融资比例提高了现金需求",
        "first_child": "第一个孩子", "marriage": "结婚", "job_loss": "失业", "rate_rise": "利率上升",
        "car_replacement": "换车",
        "SAFE": "安全", "AT_RISK": "有风险", "BREAKS": "无法承受",
        "completed": "现房", "under_construction": "在建",
    },
}


def detect_language(text: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    words = set(re.findall(r"[a-z]+", text.lower()))
    return "ms" if len(words & _MALAY) >= 2 else "en"


def prices_in(text: str) -> list[Decimal]:
    out = []
    for m in _PRICE.finditer(text):
        raw, suffix = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        value = Decimal(raw.replace(",", ""))
        out.append(value * _MULT.get((suffix or "").lower(), 1))
    return out


def intents_in(text: str) -> set[str]:
    low = text.lower()
    found = {name for name, words in INTENTS.items() if any(w in low for w in words)}
    if any(a.lower() in low for a in AREAS) and ("near" in low or "dekat" in low or "附近" in low or "show" in low):
        found.add("stock")
    return found


def _money(value: Any) -> str:
    d = Decimal(str(value))
    return f"{d:,.0f}" if d == d.to_integral_value() else f"{d:,.2f}"


def _call(name: str, **args: Any) -> ToolCall:
    return ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=name, arguments=args)


def _qty(raw: str) -> int:
    return int(raw) if raw.isdigit() else _NUMBER_WORDS[raw.lower()]


def _start_month(text: str) -> int:
    if m := _YEARS.search(text):
        return max(1, min(120, _qty(m.group(1)) * 12))
    if m := _MONTHS.search(text):
        return max(1, min(120, _qty(m.group(1))))
    return 12


def _ok(results: dict[str, list[dict[str, Any]]], name: str) -> list[dict[str, Any]]:
    return [r for r in results.get(name, []) if r.get("ok")]


def _wanted(intents: set[str], text: str, price: Decimal | None, named: Decimal | None,
            results: dict[str, list[dict[str, Any]]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    cap = _ok(results, "loan_capacity")
    capacity = Decimal(cap[0]["max_property_price"]) if cap else None
    needs_capacity = bool(intents & ({*SCENARIOS, "stock"}))

    if "afford" in intents:
        calls += [_call("compute_dsr"), _call("loan_capacity")]
        if price:
            calls.append(_call("upfront_costs", property_price=float(price)))
    elif needs_capacity:
        calls.append(_call("loan_capacity"))
    if "why" in intents:
        calls += [_call("search_regulation", query="debt service ratio net income existing commitments"),
                  _call("search_regulation", query="EPF Akaun Sejahtera housing withdrawal 2024")]
    if "epf" in intents:
        calls.append(_call("epf_withdrawal"))
    if "path" in intents and (price or named):
        calls.append(_call("plan_path_to_approval", target_property_price=float(price or named)))
    if "regulation" in intents and not calls:
        calls.append(_call("search_regulation", query=text[:300]))

    if capacity is not None and capacity > 0:
        start = _start_month(text)
        for scenario in (s for s in SCENARIOS if s in intents):
            calls.append(_call("simulate_scenario", scenario=scenario, property_price=float(capacity),
                               start_month=start))
            if named and named > capacity:
                calls.append(_call("simulate_scenario", scenario=scenario, property_price=float(named),
                                   start_month=start))
        if "stock" in intents:
            area = next((a for a in AREAS if a.lower() in text.lower()), None)
            calls.append(_call("match_affordable_stock", max_price=float(capacity), **({"area": area} if area else {})))
    return calls


def _key(call: ToolCall | dict[str, Any]) -> tuple[str, str]:
    name = call.name if isinstance(call, ToolCall) else call["name"]
    args = call.arguments if isinstance(call, ToolCall) else call["arguments"]
    return name, repr(sorted(args.items()))


def plan(messages: list[dict[str, Any]]) -> LLMResponse:
    last = max(i for i, m in enumerate(messages) if m["role"] == "user")
    text = messages[last]["content"]
    lang = detect_language(text)
    called: set[tuple[str, str]] = set()
    results: dict[str, list[dict[str, Any]]] = {}
    for m in messages[last + 1:]:
        if m["role"] == "assistant":
            called.update(_key(c) for c in m.get("tool_calls", []))
        elif m["role"] == "tool":
            results.setdefault(m["name"], []).append(m["result"])

    intents = intents_in(text)
    here = prices_in(text)
    earlier = [p for m in messages[:last] if m["role"] == "user" for p in prices_in(m["content"])]
    price = here[-1] if here else None
    named = price or (earlier[-1] if earlier else None)

    pending = [c for c in _wanted(intents, text, price, named, results) if _key(c) not in called]
    if pending:
        return LLMResponse(tool_calls=pending, stop_reason="tool_calls")
    return LLMResponse(text=compose(lang, intents, price, results), stop_reason="stop")


def compose(lang: str, intents: set[str], price: Decimal | None, results: dict[str, list[dict[str, Any]]]) -> str:
    t = T[lang]
    every = [r for rs in results.values() for r in rs]
    if any(r.get("error_code") == "PROFILE_NOT_CONFIRMED" for r in every):
        return t["not_confirmed"]
    if not every:
        return t["help"]

    lines: list[str] = []
    cap = _ok(results, "loan_capacity")
    if cap and "afford" in intents:
        c = cap[0]
        reason = t[c["limiting_factor"]]
        if price and price > Decimal(c["max_property_price"]):
            lines.append(t["no"].format(price=_money(price), max=_money(c["max_property_price"]), reason=reason))
        elif price:
            lines.append(t["yes"].format(price=_money(price), max=_money(c["max_property_price"])))
        else:
            lines.append(t["max"].format(max=_money(c["max_property_price"]), reason=reason))
        lines.append(t["loan"].format(loan=_money(c["max_loan"]), rate=c["effective_rate_pct"],
                                      tenure=c["tenure_years"], inst=_money(c["monthly_instalment"])))
    for d in _ok(results, "compute_dsr"):
        if d.get("dsr_pct") is not None:
            lines.append(t["dsr"].format(dsr=d["dsr_pct"], limit=d["max_dsr_pct"]))
    for u in _ok(results, "upfront_costs"):
        lines.append(t["upfront"].format(price=_money(u["property_price"]), total=_money(u["total_cash_needed"]),
                                         short=_money(u["shortfall_after_epf"])))
    for e in _ok(results, "epf_withdrawal"):
        lines.append(t["epf"].format(eligible=_money(e["eligible_withdrawal"]), note=e["rules_note"]))

    searches = results.get("search_regulation", [])
    hits = [h for s in searches for h in s.get("results", [])]
    if searches and not hits:
        lines.append(t["kb_miss"])
    elif hits:
        lines.append(t["why"])
        seen: set[str] = set()
        for h in hits:
            if h["doc_id"] in seen:
                continue
            seen.add(h["doc_id"])
            snippet = re.split(r"(?<=[.!?])\s", h["content"].strip(), maxsplit=1)[0]
            lines.append(t["cite"].format(title=h["title"], section=h["section"], snippet=snippet))

    for s in _ok(results, "simulate_scenario"):
        line = t["scenario"].format(label=t[s["scenario"]], price=_money(s["property_price"]), status=t[s["status"]])
        if s["status"] == "BREAKS":
            line += t["breaks"].format(month=s["break_month"], short=_money(s["monthly_shortfall"]))
        else:
            line += t["at_risk"] if s["status"] == "AT_RISK" else t["safe"]
        lines.append(line)

    for p in _ok(results, "plan_path_to_approval"):
        lines.append(t["path"].format(target=_money(p["target_property_price"])))
        for a in p["actions"]:
            lines.append(t[a["action"]].format(month=a["by_month"], kind=(a.get("commitment_type") or "").replace("_", " "),
                                               amount=_money(a["amount"]), monthly=_money(a["monthly_saving"])))
        month = p["eligible_from_month"]
        lines.append(t["eligible"].format(month=month) if month is not None else t["not_eligible"])

    for st in _ok(results, "match_affordable_stock"):
        area = t["in_area"].format(area=st["area"]) if st.get("area") else ""
        if not st["listings"]:
            lines.append(t["no_stock"].format(max=_money(st["max_price"]), area=area))
            continue
        lines.append(t["stock"].format(max=_money(st["max_price"]), area=area))
        lines += [t["listing"].format(title=x["title"], area=x["area"], price=_money(x["price"]),
                                      bedrooms=x["bedrooms"], status=t.get(x["completion_status"], ""))
                  for x in st["listings"]]

    if any(r.get("ok") is False for r in every) and not lines:
        lines.append(t["failed"])
    unverified = sorted({f for r in every for f in r.get("unverified_inputs", [])})
    if unverified:
        lines.append(t["unverified"].format(fields=", ".join(unverified)))
    return "\n".join(lines) if lines else t["help"]
