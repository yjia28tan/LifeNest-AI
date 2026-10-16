"""Offline stand-in for Gemini's reasoning so the demo runs with no API key.

It chooses tools from keywords, waits for their results, then writes a reply using only numbers copied from those
results — the same contract the system prompt imposes on Gemini, so the grounding check applies unchanged.
"""

from __future__ import annotations

import re
import uuid
from decimal import ROUND_HALF_UP, Decimal
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
    "recommend": ("should i buy", "which one should", "which property should", "recommend", "patut beli",
                  "yang mana patut", "推荐", "应该买哪"),
    "when": ("when can i", "when will i", "when could i", "how long until", "how long before", "how many years",
             "bila saya", "bilakah", "什么时候", "多久"),
}
ANSWER_INTENTS = {"stated", "affirm", "deny"}
# Longest names first so "penang island" wins over "penang".
PRICE_AREAS = (("penang island", "Penang Island"), ("pulau pinang", "Penang Island"),
               ("penang mainland", "Seberang Perai"), ("seberang perai", "Seberang Perai"),
               ("butterworth", "Seberang Perai"), ("bukit mertajam", "Seberang Perai"), ("penang", "Penang Island"))
_AMOUNT = r"(?:RM\s?)?(\d[\d,]*(?:\.\d+)?)\s*(k)?"
_INCOME_AFTER = re.compile(r"(?:salary|gaji|earn|income is|income of|pendapatan|工资|薪水|月薪|收入)"
                           r"[\sA-Za-z:\u4e00-\u9fff]{0,15}?" + _AMOUNT + r"(?!\d)", re.IGNORECASE)
_INCOME_BEFORE = re.compile(_AMOUNT + r"\s*(?:ringgit\s+)?(?:(?:net|nett|gross)\s+)?(?:salary|gaji)", re.IGNORECASE)
_AGE = re.compile(r"\b(?:i'?m|i am|aged?|umur|berumur)\s*:?\s*(\d{2})\b|\b(\d{2})\s*(?:years? old|岁)",
                  re.IGNORECASE)
_SAVINGS = re.compile(r"(?:i have saved|i've saved|my savings (?:are|is)|simpanan saya)\s*:?\s*" + _AMOUNT,
                      re.IGNORECASE)
_PROPERTY_NO = re.compile(r"\b(2nd|second|3rd|third|4th|fourth)\b|kedua|ketiga|第二|第三", re.IGNORECASE)
_NET = re.compile(r"\bnett?\b|take[- ]?home|\bbersih\b|净|到手|实收", re.IGNORECASE)
_YES = re.compile(r"^\s*(?:(?:yes|yep|yup|ya|correct|betul|ok|okay|still correct)\b|对|是)", re.IGNORECASE)
_NO = re.compile(r"^\s*(no|nope|tidak|bukan|wrong|salah|不)\b", re.IGNORECASE)
SCENARIOS = ("first_child", "marriage", "job_loss", "rate_rise", "car_replacement")

T: dict[str, dict[str, str]] = {
    "en": {
        "not_confirmed": "Before I can work out any numbers I need your latest payslip and your EPF statement. "
                         "Upload them and I'll show you exactly what I read so you can check it first.",
        "no": "{Reason}: on today's figures the price tops out at RM{max}, below RM{price}.",
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
        "REACH_NET_INCOME": "• Net income would need to reach RM{amount} a month.",
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
        "stated": "I've noted {fields} as a figure you stated, not one read from a document.",
        "recommend": "I can't tell you which property to buy — that's your decision, and I'm not a sales channel. "
                     "What I can do is show what you can genuinely afford and how a price holds up against life "
                     "events. Would you like me to check a specific price?",
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
        "no": "{Reason}: berdasarkan angka hari ini, harga maksimum ialah RM{max}, di bawah RM{price}.",
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
        "REACH_NET_INCOME": "• Pendapatan bersih perlu mencapai RM{amount} sebulan.",
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
        "stated": "Saya telah mencatat {fields} sebagai angka yang anda nyatakan, bukan dibaca daripada dokumen.",
        "recommend": "Saya tidak boleh memberitahu hartanah mana yang patut dibeli — itu keputusan anda, dan saya "
                     "bukan saluran jualan. Saya boleh tunjukkan apa yang anda benar-benar mampu dan bagaimana "
                     "sesuatu harga bertahan terhadap peristiwa hidup. Mahu saya semak harga tertentu?",
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
        "no": "{Reason}：按目前的数字，最高房价为 RM{max}，低于 RM{price}。",
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
        "REACH_NET_INCOME": "• 每月净收入需要达到 RM{amount}。",
        "REDUCE_TARGET_PRICE": "• 在规划期内，您最多可以达到 RM{amount}。",
        "eligible": "按这些假设，您可能从第 {month} 个月起符合条件。",
        "not_eligible": "按这些假设，您在规划期内达不到该价格。",
        "stock": "不超过您计算出的 RM{max} 的房源{area}：",
        "listing": "• {title}，{area}：RM{price}（{bedrooms} 房，{status}）",
        "no_stock": "没有找到不超过 RM{max} 的房源{area}。",
        "kb_miss": "我的知识库没有涵盖这一点，所以我不会猜测。请向相关机构（马来西亚国家银行、公积金局或内陆税收局）核实。",
        "unverified": "注意：这里使用了您告诉我的数字（{fields}），并非从文件中读取，因此结果未经核实。",
        "failed": "我无法计算这一部分，所以不会给出数字。",
        "stated": "我已将 {fields} 记录为您声明的数字，而非从文件中读取。",
        "recommend": "我不能告诉您应该买哪一间——这是您的决定，我也不是销售渠道。我可以告诉您真正负担得起的价格，"
                     "以及某个价格在人生事件下是否稳健。要我帮您查一个具体价格吗？",
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

_T_MORE: dict[str, dict[str, str]] = {
    "en": {
        "ask_lead": "Before I put any figures on this, I need to check:",
        "or_upload": "Or upload your payslip and EPF statement and I'll read them for you.",
        "q_income_basis": "Is that your gross salary (before EPF and tax) or your net take-home pay?",
        "q_missing_age": "How old are you? A home loan must end by a set age, so your age sets the longest tenure.",
        "q_missing_income": "What is your monthly salary? (Gross, before EPF and tax, unless you say net.)",
        "q_property_count": "You have an existing home loan ({value}). Would this home be your 2nd property, or "
                            "your 3rd or later? It sets the margin of finance and whether first-home incentives "
                            "apply.",
        "q_reconfirm": "I have your {label} as {value} from earlier. Is that still correct?",
        "q_inferred": "I don't have a {label} you confirmed. What is it?",
        "kept": "Thanks — I'll use those figures.",
        "removed": "Understood — I've removed those figures. Please tell me the current ones.",
        "income_derived": "Net income used: RM{net} a month, estimated from your RM{gross} gross salary after "
                          "EPF, SOCSO, EIS and PCB.",
        "tenure": "Tenure: {reason}",
        "tl_head": "When you could afford it, by scenario:",
        "tl_cols": "| Scenario | Target price | Earliest date | Key lever |",
        "tl_baseline": "Baseline", "tl_clear_commitments": "Clear commitments",
        "tl_joint_applicant": "Joint applicant", "tl_cheaper_area": "Cheaper area",
        "tl_not_within": "not within {years} years", "tl_needs": "needs: {inputs}", "tl_na": "not applicable",
        "tl_binding": "Binding constraint today: {reason}.",
        "tl_band": "The {area} target is a typical entry price from the rules config, not a listing.",
        "reach_caps": "• That income falls in a band with a {cap_req}% DSR cap; today's income band allows "
                      "{cap_now}%. Both come from the same income-band table.",
        "savings_target": "• Savings target: RM{monthly} a month for {months} months to hold RM{total} "
                          "(deposit, legal fees, stamp duty, valuation, disbursements and a {buffer}-month buffer).",
        "inputs": "Figures used: {items}.",
        "prov_stated_this_turn": "you stated", "prov_stated_earlier": "you stated earlier",
        "prov_confirmed_from_profile": "confirmed from {source}", "prov_default": "rules default",
        "prov_inferred": "unconfirmed",
    },
    "ms": {
        "ask_lead": "Sebelum saya memberi apa-apa angka, saya perlu semak:",
        "or_upload": "Atau muat naik slip gaji (payslip) dan penyata KWSP (EPF) anda dan saya akan membacanya.",
        "q_income_basis": "Adakah itu gaji kasar (sebelum KWSP dan cukai) atau gaji bersih yang anda terima?",
        "q_missing_age": "Berapakah umur anda? Pinjaman perumahan mesti tamat sebelum had umur, jadi umur "
                         "menentukan tempoh maksimum.",
        "q_missing_income": "Berapakah gaji bulanan anda? (Kasar, sebelum KWSP dan cukai, melainkan anda kata bersih.)",
        "q_property_count": "Anda ada pinjaman perumahan sedia ada ({value}). Adakah rumah ini hartanah ke-2, "
                            "atau ke-3 dan seterusnya? Ini menentukan margin pembiayaan dan insentif rumah pertama.",
        "q_reconfirm": "Saya ada {label} anda sebagai {value} dari tadi. Adakah ia masih betul?",
        "q_inferred": "Saya tiada {label} yang anda sahkan. Berapakah nilainya?",
        "kept": "Terima kasih — saya akan guna angka itu.",
        "removed": "Baik — saya telah buang angka itu. Sila beritahu angka terkini.",
        "income_derived": "Pendapatan bersih digunakan: RM{net} sebulan, dianggarkan daripada gaji kasar RM{gross} "
                          "selepas KWSP, PERKESO, SIP dan PCB.",
        "tenure": "Tempoh: {reason}",
        "tl_head": "Bila anda mungkin mampu, mengikut senario:",
        "tl_cols": "| Senario | Harga sasaran | Tarikh terawal | Tuas utama |",
        "tl_baseline": "Asas", "tl_clear_commitments": "Langsaikan komitmen",
        "tl_joint_applicant": "Pemohon bersama", "tl_cheaper_area": "Kawasan lebih murah",
        "tl_not_within": "tidak dalam {years} tahun", "tl_needs": "perlu: {inputs}", "tl_na": "tidak berkaitan",
        "tl_binding": "Kekangan utama hari ini: {reason}.",
        "tl_band": "Sasaran {area} ialah harga permulaan biasa daripada konfigurasi peraturan, bukan senarai.",
        "reach_caps": "• Pendapatan itu berada dalam julat dengan had DSR {cap_req}%; julat hari ini {cap_now}%. "
                      "Kedua-duanya daripada jadual julat pendapatan yang sama.",
        "savings_target": "• Sasaran simpanan: RM{monthly} sebulan selama {months} bulan untuk RM{total} "
                          "(deposit, yuran guaman, duti setem, penilaian, kos lain dan penampan {buffer} bulan).",
        "inputs": "Angka digunakan: {items}.",
        "prov_stated_this_turn": "anda nyatakan", "prov_stated_earlier": "anda nyatakan tadi",
        "prov_confirmed_from_profile": "disahkan daripada {source}", "prov_default": "lalai peraturan",
        "prov_inferred": "belum disahkan",
    },
    "zh": {
        "ask_lead": "在给出任何数字之前，我需要确认：",
        "or_upload": "或者上传您的工资单（payslip）和公积金（EPF）结单，我来读取。",
        "q_income_basis": "这是您的税前总薪资（扣除公积金和税之前），还是到手净薪？",
        "q_missing_age": "请问您几岁？房贷必须在规定年龄前还清，所以年龄决定最长贷款年期。",
        "q_missing_income": "您的月薪是多少？（除非您说明是净薪，否则按税前总薪计算。）",
        "q_property_count": "您已有一笔房屋贷款（{value}）。这间房子是您的第 2 间，还是第 3 间或以上？这决定融资比例以及能否享受首购优惠。",
        "q_reconfirm": "我这里记录的{label}为 {value}。现在仍然正确吗？",
        "q_inferred": "我没有您确认过的{label}。请问是多少？",
        "kept": "谢谢——我会使用这些数字。",
        "removed": "明白——我已删除这些数字。请告诉我最新的数字。",
        "income_derived": "所用净收入：每月 RM{net}，由您的税前月薪 RM{gross} 扣除公积金、SOCSO、EIS 和 PCB 估算。",
        "tenure": "贷款年期：{reason}",
        "tl_head": "各情景下最早可负担的时间：",
        "tl_cols": "| 情景 | 目标价格 | 最早日期 | 关键因素 |",
        "tl_baseline": "基准", "tl_clear_commitments": "还清现有债务", "tl_joint_applicant": "联名申请",
        "tl_cheaper_area": "较便宜地区",
        "tl_not_within": "{years} 年内无法达到", "tl_needs": "需要：{inputs}", "tl_na": "不适用",
        "tl_binding": "目前的限制因素：{reason}。",
        "tl_band": "{area} 的目标价格是规则配置中的典型入门价，并非具体房源。",
        "reach_caps": "• 该收入所在区间的 DSR 上限为 {cap_req}%；目前收入区间为 {cap_now}%。两者来自同一收入区间表。",
        "savings_target": "• 储蓄目标：每月 RM{monthly}，共 {months} 个月，以备好 RM{total}（首付、律师费、印花税、估价、其他费用及 {buffer} 个月缓冲）。",
        "inputs": "所用数字：{items}。",
        "prov_stated_this_turn": "您刚提供", "prov_stated_earlier": "您之前提供",
        "prov_confirmed_from_profile": "已由{source}确认", "prov_default": "规则默认",
        "prov_inferred": "未确认",
    },
}
for _lang, _extra in _T_MORE.items():
    T[_lang].update(_extra)


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


def _amount(m: re.Match[str], first: int = 1) -> float:
    value = Decimal(m.group(first).replace(",", ""))
    return float(value * (1000 if m.group(first + 1) else 1))


def stated_figures(text: str) -> dict[str, Any]:
    """Figures the user wrote about themselves in this message: only what is literally there."""
    out: dict[str, Any] = {}
    if m := _INCOME_AFTER.search(text) or _INCOME_BEFORE.search(text):
        out["income.net_monthly" if _NET.search(text) else "income.gross_monthly"] = _amount(m)
    if m := _AGE.search(text):
        out["person.age"] = int(m.group(1) or m.group(2))
    if m := _SAVINGS.search(text):
        out["savings"] = _amount(m)
    if (m := _PROPERTY_NO.search(text)) and re.search(r"propert|home|house|rumah|房", text, re.IGNORECASE):
        word = m.group(0).lower()
        out["existing_housing_loans"] = 1 if word in ("2nd", "second", "kedua", "第二") else 2
    return out


def price_area(text: str) -> str | None:
    low = text.lower()
    return next((name for alias, name in PRICE_AREAS if alias in low), None)


def intents_in(text: str) -> set[str]:
    low = text.lower()
    found = {name for name, words in INTENTS.items() if any(w in low for w in words)}
    if any(a.lower() in low for a in AREAS) and ("near" in low or "dekat" in low or "附近" in low or "show" in low):
        found.add("stock")
    if stated_figures(text):
        found.add("stated")
    if _YES.search(text):
        found.add("affirm")
    elif _NO.search(text):
        found.add("deny")
    return found


def _money(value: Any) -> str:
    """Narrative rounding: nearest RM100 (whole ringgit below RM100). Cards show the exact figure."""
    d = Decimal(str(value))
    if abs(d) >= 100:
        return f"{(d / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 100:,.0f}"
    return f"{d.quantize(Decimal(1), rounding=ROUND_HALF_UP):,.0f}"


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


def _wanted(intents: set[str], text: str, price: Decimal | None, named: Decimal | None, area: str | None,
            stated: dict[str, Any], results: dict[str, list[dict[str, Any]]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    if "recommend" in intents:
        return calls
    if stated and "record_stated_figures" not in results:
        return [_call("record_stated_figures", figures=stated)]
    if intents & {"affirm", "deny"} and "confirm_stored_figures" not in results:
        return [_call("confirm_stored_figures", paths=[], still_correct="affirm" in intents)]
    if any(r.get("needs_confirmation") for r in _ok(results, "record_stated_figures")):
        return calls
    cap = _ok(results, "loan_capacity")
    capacity = Decimal(cap[0]["max_property_price"]) if cap else None
    needs_capacity = bool(intents & ({*SCENARIOS, "stock"}))

    if "when" in intents and (named or area):
        args: dict[str, Any] = {"target_property_price": float(named)} if named else {}
        calls.append(_call("affordability_timeline", **args, **({"area": area} if area else {})))
        intents = intents - {"afford"}
    if "afford" in intents:
        calls += [_call("compute_dsr"), _call("loan_capacity")]
        if price:
            calls.append(_call("upfront_costs", property_price=float(price)))
            if capacity is not None and capacity < price:
                calls.append(_call("plan_path_to_approval", target_property_price=float(price)))
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


def _prices_excluding_stated(text: str) -> list[Decimal]:
    stated = {Decimal(str(v)) for v in stated_figures(text).values()}
    return [p for p in prices_in(text) if p not in stated]


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
    stated = stated_figures(text)
    prev_users = [m["content"] for m in messages[:last] if m["role"] == "user"]
    if intents and intents <= ANSWER_INTENTS:
        # The message only answers our question, so resume the question the user asked before it.
        for prev in reversed(prev_users):
            carried = intents_in(prev) - ANSWER_INTENTS
            if carried:
                intents |= carried
                break
        else:
            if "stated" in intents:
                intents.add("afford")
    here = _prices_excluding_stated(text)
    earlier = [p for prev in prev_users for p in _prices_excluding_stated(prev)]
    price = here[-1] if here else None
    named = price or (earlier[-1] if earlier else None)
    area = price_area(text) or next((a for prev in reversed(prev_users) if (a := price_area(prev))), None)

    pending = [c for c in _wanted(intents, text, price, named, area, stated, results) if _key(c) not in called]
    if pending:
        return LLMResponse(tool_calls=pending, stop_reason="tool_calls")
    return LLMResponse(text=compose(lang, intents, price, results), stop_reason="stop")


def _question(t: dict[str, str], q: dict[str, Any]) -> str:
    reason, field = q["reason"], q["field"]
    if reason == "income_basis":
        return t["q_income_basis"]
    if reason == "missing":
        return t["q_missing_age"] if field == "person.age" else t["q_missing_income"]
    if reason == "property_count":
        return t["q_property_count"].format(value=q["value"])
    if reason == "not_confirmed_this_session":
        return t["q_reconfirm"].format(label=q.get("label", field), value=q["value"])
    return t["q_inferred"].format(label=q.get("label", field))


def _round_rm(text: str) -> str:
    return re.sub(r"RM([\d,]+(?:\.\d+)?)", lambda m: "RM" + _money(m.group(1).replace(",", "")), text)


def _provenance(t: dict[str, str], item: dict[str, Any]) -> str:
    return t[f"prov_{item['provenance']}"].format(source=item["source"].replace("_", " "))


def _timeline_lines(t: dict[str, str], tl: dict[str, Any]) -> list[str]:
    lines = [t["tl_head"], t["tl_cols"], "|---|---|---|---|"]
    for row in tl["rows"]:
        name = t[f"tl_{row['scenario']}"]
        if row["scenario"] == "cheaper_area" and row["area"]:
            name += f" ({row['area']})"
        when = {
            "AFFORDABLE": row["earliest_date"],
            "NOT_WITHIN_HORIZON": t["tl_not_within"].format(years=tl["horizon_years"]),
            "NEEDS_INPUTS": t["tl_needs"].format(inputs=", ".join(row["inputs_needed"])),
            "NOT_APPLICABLE": t["tl_na"],
        }[row["status"]]
        target = f"RM{_money(row['target_price'])}" if row["target_price"] else "—"
        lines.append(f"| {name} | {target} | {when} | {row['key_lever']} |")
    lines += ["", t["tl_binding"].format(reason=t[tl["binding_constraint"]])]
    if tl["target_price_source"] == "price_band" and tl["target_area"]:
        lines.append(t["tl_band"].format(area=tl["target_area"]))
    return lines


def _income_line(t: dict[str, str], income: dict[str, Any] | None) -> list[str]:
    if not income or income["basis"] != "derived_from_gross":
        return []
    return [t["income_derived"].format(net=_money(income["net_monthly"]), gross=_money(income["gross_monthly"]))]


def compose(lang: str, intents: set[str], price: Decimal | None, results: dict[str, list[dict[str, Any]]]) -> str:
    t = T[lang]
    if "recommend" in intents:
        return t["recommend"]
    every = [r for rs in results.values() for r in rs]
    if not every:
        return t["help"]

    lines: list[str] = []
    for s in _ok(results, "record_stated_figures"):
        if s["recorded"]:
            lines.append(t["stated"].format(fields=", ".join(s["recorded"])))
    for c in _ok(results, "confirm_stored_figures"):
        if c.get("kept") or c.get("removed"):
            lines.append(t["kept"] if c.get("kept") else t["removed"])

    questions: list[dict[str, Any]] = []
    for q in (q for r in every for q in r.get("needs_confirmation", [])):
        if (q["field"], q["reason"]) not in {(x["field"], x["reason"]) for x in questions}:
            questions.append(q)
    if questions:
        lines.append(t["ask_lead"])
        lines += [f"• {_question(t, q)}" for q in questions[:3]]
        if any(q["reason"] == "missing" for q in questions):
            lines.append(t["or_upload"])
        return "\n".join(lines)
    if any(r.get("error_code") == "PROFILE_NOT_CONFIRMED" for r in every):
        return t["not_confirmed"]

    for tl in _ok(results, "affordability_timeline"):
        lines += _timeline_lines(t, tl)
        lines += _income_line(t, tl["income"])
        lines.append(t["tenure"].format(reason=tl["tenure_reason"]))

    cap = _ok(results, "loan_capacity")
    if cap and "afford" in intents:
        c = cap[0]
        reason = t[c["limiting_factor"]]
        if price and price > Decimal(c["max_property_price"]):
            lines.append(t["no"].format(price=_money(price), max=_money(c["max_property_price"]),
                                        Reason=reason[:1].upper() + reason[1:]))
        elif price:
            lines.append(t["yes"].format(price=_money(price), max=_money(c["max_property_price"])))
        else:
            lines.append(t["max"].format(max=_money(c["max_property_price"]), reason=reason))
        lines.append(t["loan"].format(loan=_money(c["max_loan"]), rate=c["effective_rate_pct"],
                                      tenure=c["tenure_years"], inst=_money(c["monthly_instalment"])))
        lines.append(t["tenure"].format(reason=c["tenure_reason"]))
    for d in _ok(results, "compute_dsr"):
        if d.get("dsr_pct") is not None:
            lines.append(t["dsr"].format(dsr=d["dsr_pct"], limit=d["max_dsr_pct"]))
        lines += _income_line(t, d.get("income"))
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
            caps = (p["dsr_cap_pct_at_required_income"], p["dsr_cap_pct_at_current_income"])
            if a["action"] == "REACH_NET_INCOME" and caps[0] and caps[0] != caps[1]:
                lines.append(t["reach_caps"].format(cap_req=caps[0], cap_now=caps[1]))
        st = p["savings_target"]
        if Decimal(st["gap"]) > 0:
            lines.append(t["savings_target"].format(monthly=_money(st["monthly_savings_target"]),
                                                    months=st["months_to_target"], total=_money(st["total_needed"]),
                                                    buffer=st["buffer_months"]))
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
    used = next((r["inputs_used"] for r in every if r.get("ok") and r.get("inputs_used")), None)
    if used and lines:
        items = "; ".join(f"{i['label']} {_round_rm(i['value'])} ({_provenance(t, i)})" for i in used)
        lines.append(t["inputs"].format(items=items))
    unverified = sorted({f for r in every for f in r.get("unverified_inputs", [])})
    if unverified:
        lines.append(t["unverified"].format(fields=", ".join(unverified)))
    return "\n".join(lines) if lines else t["help"]
