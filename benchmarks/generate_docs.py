"""Generate a labelled benchmark set of synthetic payslips, EPF statements and loan statements.

Every value printed on a document is also written to ground_truth.json, so extraction can be scored field by field.
All names, employers and figures are fictional.

Usage:
    python -m benchmarks.generate_docs                    # 20 personas -> 60 documents
    python -m benchmarks.generate_docs --personas 30 --photo-share 0.4 --seed 7
"""

from __future__ import annotations

import argparse
import io
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from benchmarks.common import DEFAULT_DATA_DIR
from scripts.generate_mock_documents import (
    INK,
    MUTED,
    Payslip,
    _font,
    _rm,
    _row,
    _rule,
    _watermark,
    render_payslip,
)

FIRST = ["Aina", "Farah", "Hafiz", "Siti", "Daniel", "Wei Ling", "Jun Hao", "Priya", "Arjun", "Nurul", "Iskandar",
         "Mei Xin", "Kavitha", "Amirul", "Chloe", "Ravi", "Syafiq", "Hui Min", "Deepa", "Zul"]
LAST = ["Contoh", "Sampel", "Demo", "Ujian", "Rekaan", "Fiktif"]
EMPLOYERS = ["Contoh Teknologi Sdn Bhd", "Sampel Logistik Sdn Bhd", "Demo Retail Solutions Sdn Bhd",
             "Fiktif Engineering Sdn Bhd", "Ujian Healthcare Sdn Bhd", "Rekaan Media Sdn Bhd",
             "Contoh Hartanah Berhad", "Sampel Fintech Sdn Bhd"]
POSITIONS = ["Software Engineer", "Account Executive", "Nurse", "Operations Manager", "Sales Executive",
             "Graphic Designer", "Lecturer", "Data Analyst", "Civil Engineer", "Customer Service Lead"]
LOAN_TYPES = [
    ("car_loan", "HIRE PURCHASE - Bank Contoh Berhad", "Monthly Instalment / Ansuran Bulanan", (450, 1600), (6, 84)),
    ("study_loan", "STUDY LOAN - Tabung Pendidikan Contoh", "Monthly Repayment / Bayaran Bulanan", (150, 450), (12, 120)),
    ("personal_loan", "PERSONAL FINANCING - Bank Sampel Berhad", "Monthly Instalment / Ansuran Bulanan",
     (300, 1500), (6, 60)),
]


@dataclass
class Loan:
    type: str
    monthly: float
    remaining_months: int


def _money(x: float) -> float:
    return round(x + 1e-9, 2)


def make_payslip(rng: random.Random, idx: int, name: str, age: int) -> Payslip:
    basic = rng.randrange(2500, 14000, 50)
    earnings: list[tuple[str, float]] = [("Basic Salary / Gaji Pokok", float(basic))]
    if rng.random() < 0.7:
        earnings.append(("Fixed Allowance / Elaun Tetap", float(rng.choice([150, 200, 300, 400, 500, 800, 1000]))))
    # At most one variable line, so "variable_component" has one printed value and needs no arithmetic.
    roll = rng.random()
    if roll < 0.25:
        earnings.append(("Sales Commission / Komisen", float(rng.randrange(300, 4000, 10))))
    elif roll < 0.40:
        earnings.append(("Overtime / Kerja Lebih Masa", _money(rng.uniform(80, 900))))
    gross = sum(v for _, v in earnings)
    capped = min(gross, 6000)
    epf = float(math.ceil(gross * 0.11))
    socso = _money(capped * 0.005)
    eis = _money(capped * 0.002)
    taxable = max(gross * 12 - 9000 - min(epf * 12, 4000) - 34000, 0)
    pcb = _money(max(taxable * rng.uniform(0.055, 0.11) / 12, 0))
    return Payslip(filename=f"p{idx:03d}_payslip", employer=rng.choice(EMPLOYERS), employee=name, age=age,
                   position=rng.choice(POSITIONS), earnings=earnings, epf=epf, socso=socso, eis=eis, pcb=pcb)


def render_epf(name: str, balances: dict[str, float], date: str) -> Image.Image:
    w, h = 900, 760
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.text((60, 50), "EPF Member Statement (synthetic)", font=_font(30), fill=INK)
    d.text((60, 92), "Penyata Ahli KWSP - three-account structure (2024)", font=_font(16), fill=MUTED)
    y = _rule(d, 135, w)
    y = _row(d, y, "Member / Ahli", name, w, 18)
    y = _row(d, y, "Member No. / No. Ahli", "00000000", w, 18)
    y = _row(d, y, "Statement date", date, w, 18)
    y = _rule(d, y + 6, w)
    y = _row(d, y, "ACCOUNT BALANCES / BAKI AKAUN", "", w, 20, MUTED)
    for label, value in balances.items():
        y = _row(d, y, label, _rm(value), w, 22)
    y = _rule(d, y + 6, w)
    _row(d, y, "Total / Jumlah", _rm(sum(balances.values())), w, 26)
    d.text((60, h - 50), "Synthetic statement for demonstration only.", font=_font(14), fill=MUTED)
    return img


def render_loans(name: str, loans: list[Loan]) -> Image.Image:
    w, h = 900, 260 + 230 * len(loans)
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.text((60, 50), "Loan Statements (synthetic)", font=_font(30), fill=INK)
    d.text((60, 92), f"Borrower: {name}", font=_font(16), fill=MUTED)
    y = _rule(d, 135, w)
    spec = {t[0]: t for t in LOAN_TYPES}
    for loan in loans:
        _, header, label, _, _ = spec[loan.type]
        y = _row(d, y, header, "", w, 20, MUTED)
        y = _row(d, y, "Account No.", "0000-0000-0000", w, 18)
        y = _row(d, y, label, _rm(loan.monthly), w, 22)
        y = _row(d, y, "Remaining Instalments / Baki Ansuran", str(loan.remaining_months), w)
        y = _rule(d, y + 10, w)
    d.text((60, h - 50), "Synthetic statement for demonstration only.", font=_font(14), fill=MUTED)
    return img


def phone_photo(img: Image.Image, rng: random.Random) -> Image.Image:
    """A mild 'photographed on a phone' look: slight tilt, uneven light, soft focus. Much gentler than the repo's
    low_quality_payslip, which is deliberately unreadable."""
    out = img.rotate(rng.uniform(-3, 3), resample=Image.BICUBIC, expand=True, fillcolor=(235, 232, 225))
    out = ImageEnhance.Brightness(out).enhance(rng.uniform(0.85, 1.05))
    out = ImageEnhance.Contrast(out).enhance(rng.uniform(0.75, 0.95))
    return out.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.8)))


def save(img: Image.Image, path_stem: Path, photo: bool, rng: random.Random) -> tuple[str, str]:
    img = _watermark(img)
    if photo:
        img = phone_photo(img, rng)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=72)
        path = path_stem.with_suffix(".jpg")
        path.write_bytes(buf.getvalue())
        return path.name, "image/jpeg"
    path = path_stem.with_suffix(".png")
    img.save(path, format="PNG", optimize=True)
    return path.name, "image/png"


def generate(out_dir: Path, personas: int, photo_share: float, seed: int) -> dict:
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    truth: dict = {"seed": seed, "note": "Synthetic benchmark set. Values are exactly as printed.",
                   "documents": {}, "personas": {}}

    for i in range(1, personas + 1):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        age = rng.randint(23, 58)
        pid = f"p{i:03d}"

        slip = make_payslip(rng, i, name, age)
        variable = sum(v for label, v in slip.earnings if "Komisen" in label or "Lebih Masa" in label)
        photo = rng.random() < photo_share
        fname, ctype = save(render_payslip(slip), out_dir / f"{pid}_payslip", photo, rng)
        truth["documents"][fname] = {
            "persona": pid, "kind": "payslip", "content_type": ctype, "variant": "photo" if photo else "clean",
            "fields": {"person.age": age, "income.gross_monthly": f"{slip.gross:.2f}",
                       "income.statutory_deductions": f"{slip.deductions:.2f}",
                       "income.variable_component": f"{variable:.2f}", "income.net_monthly": f"{slip.net:.2f}"},
        }
        docs = [fname]

        sejahtera = float(rng.randrange(1500, 90000, 10)) + rng.choice([0, 0.25, 0.5, 0.8])
        balances = {"Akaun Persaraan (75%)": _money(sejahtera * rng.uniform(4.2, 5.8)),
                    "Akaun Sejahtera (15%)": _money(sejahtera),
                    "Akaun Fleksibel (10%)": _money(sejahtera * rng.uniform(0.1, 0.7))}
        photo = rng.random() < photo_share
        fname, ctype = save(render_epf(name, balances, "30 September 2026"), out_dir / f"{pid}_epf", photo, rng)
        truth["documents"][fname] = {
            "persona": pid, "kind": "epf_statement", "content_type": ctype, "variant": "photo" if photo else "clean",
            "fields": {"epf.akaun_sejahtera": f"{sejahtera:.2f}"},
        }
        docs.append(fname)

        loans = []
        for loan_type, _, _, (lo, hi), (mlo, mhi) in rng.sample(LOAN_TYPES, k=rng.randint(1, 3)):
            loans.append(Loan(loan_type, float(rng.randrange(lo, hi, 5)), rng.randint(mlo, mhi)))
        photo = rng.random() < photo_share
        fname, ctype = save(render_loans(name, loans), out_dir / f"{pid}_loans", photo, rng)
        truth["documents"][fname] = {
            "persona": pid, "kind": "loan_statement", "content_type": ctype, "variant": "photo" if photo else "clean",
            "fields": {}, "commitments": [{**asdict(x), "monthly": f"{x.monthly:.2f}"} for x in loans],
        }
        docs.append(fname)
        truth["personas"][pid] = {"documents": docs, "age": age}

    (out_dir / "ground_truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    return truth


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--personas", type=int, default=20)
    parser.add_argument("--photo-share", type=float, default=0.3,
                        help="share of documents saved as a mildly degraded phone-photo JPEG (default 0.3)")
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    truth = generate(args.out, args.personas, args.photo_share, args.seed)
    docs = truth["documents"].values()
    n_fields = sum(len(d["fields"]) + 2 * len(d.get("commitments", [])) for d in docs)
    photos = sum(d["variant"] == "photo" for d in docs)
    print(f"wrote {len(truth['documents'])} documents ({photos} phone-photo style), {n_fields} labelled fields "
          f"-> {args.out}")


if __name__ == "__main__":
    main()
