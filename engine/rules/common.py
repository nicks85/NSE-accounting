"""Citations shared by the rule packs. Section numbers for the 2025 Act are verified against
docs/sources/income-tax-act-2025-as-amended-by-fa-2026.pdf; 1961 Act numbers are not yet
checked against an official 1961 text (docs/OPEN_QUESTIONS.md Q-001)."""

from engine.rules.base import ACT_2025_URL, CBDT_CG_FAQ_URL, CBDT_CIRCULAR_768_URL, Citation

HOLDING_PERIOD = Citation(
    "Listed shares are short-term if held for not more than 12 months",
    "s.2(42A)", "s.2(101)(a),(b)", CBDT_CG_FAQ_URL)
HOLDING_BOUNDARY = Citation(
    "Sale within a few days of the 12-month boundary: day-count convention assumed",
    "s.2(42A)", "s.2(101)(a),(b)", ACT_2025_URL, unverified=True, question="Q-013")
FIFO = Citation("FIFO for demat securities", "s.45(2A)", "s.67(7)(c)", ACT_2025_URL)
FIFO_PER_ACCOUNT = Citation(
    "FIFO applied separately in each demat account; shares moved in from another of your "
    "accounts queue by the date they entered (CBDT Circular 768) but keep their purchase date "
    "and cost", "s.45(2A); Circular 768", "s.67(7)(c)", CBDT_CIRCULAR_768_URL,
    unverified=True, question="Q-026")
COMPUTATION = Citation(
    "Gain = sale value - transfer expenses - cost; STT not deductible",
    "s.48", "s.72(1), s.72(3)(b)", ACT_2025_URL)
GRANDFATHERING = Citation(
    "Shares acquired before 1-Feb-2018: cost = higher of actual cost and lower of "
    "31-Jan-2018 FMV and sale value",
    "s.55(2)(ac)", "s.90(7), s.90(8)(b)", ACT_2025_URL)
GRANDFATHERING_AFTER_SPLIT = Citation(
    "31-Jan-2018 FMV divided by later split/consolidation ratios",
    "s.55(2)(ac)", "s.90(7), s.90(8)(b)", ACT_2025_URL, unverified=True, question="Q-006")
EQUITY_FUND = Citation(
    "Equity-oriented fund units taxed like listed shares (12 months, special rates)",
    "s.111A, s.112A", "s.196, s.198, s.198(8)", ACT_2025_URL)
SPECIFIED_FUND = Citation(
    "Specified (debt) fund units bought on or after 1-Apr-2023: always short-term, slab rate",
    "s.50AA", "s.76", ACT_2025_URL)
UNLISTED_HOLDING = Citation(
    "Non-equity fund units are long-term only if held for more than 24 months",
    "s.2(42A)", "s.2(101)(a)", ACT_2025_URL)
OTHER_LTCG = Citation(
    "Other long-term capital gains at 12.5% without indexation (no ₹1.25 lakh exemption)",
    "s.112", "s.197(1)", ACT_2025_URL)
SLAB_STCG = Citation(
    "Other short-term capital gains are taxed at the normal slab rates", None, None,
    ACT_2025_URL)
FUND_CLASS = Citation(
    "Fund class taken from the user's classification (or a name-based guess)", None,
    "s.76, s.198", ACT_2025_URL, unverified=True, question="Q-019")
FUND_FIFO_PER_FOLIO = Citation(
    "Fund units matched first-in-first-out within each folio", None, None, ACT_2025_URL,
    unverified=True, question="Q-020")
LISTED_FUND_UNITS = Citation(
    "Exchange-traded non-equity fund units treated like unlisted units (24 months)",
    "s.2(42A)", "s.2(101)(a),(b)", ACT_2025_URL, unverified=True, question="Q-024")
SPECIFIED_DEFINITION_1961 = Citation(
    "Before FY 2025-26 a 'specified mutual fund' meant one with at most 35% in domestic "
    "equity; check the class supplied", "s.50AA", None, ACT_2025_URL, unverified=True,
    question="Q-019")
STCG_EQUITY = Citation("STCG on STT-paid equity", "s.111A", "s.196", CBDT_CG_FAQ_URL)
LTCG_EQUITY = Citation(
    "LTCG on STT-paid equity above the exemption", "s.112A", "s.198", CBDT_CG_FAQ_URL)
STT_PAID_ASSUMED = Citation(
    "Disposals assumed STT-paid on acquisition and transfer (conditions of the special rates)",
    "s.111A(1)(b), s.112A(1)", "s.196(1)(b), s.198(1)(c)", ACT_2025_URL,
    unverified=True, question="Q-009")
SPECULATIVE = Citation(
    "Intraday equity is a speculative transaction", "s.43(5)", "s.66(31)", ACT_2025_URL)
NON_SPECULATIVE = Citation(
    "Exchange-traded derivatives are not speculative", "s.43(5) proviso (d)",
    "s.66(31)(a), s.66(33)", ACT_2025_URL)
STT_BUSINESS_DEDUCTION = Citation(
    "STT on business transactions is deductible", "s.36(1)(xv)", "s.32(k)", ACT_2025_URL)
SETOFF_CAPITAL = Citation(
    "Short-term capital loss against any capital gain; long-term loss only against LTCG",
    "s.70(2),(3)", "s.108(2)", ACT_2025_URL)
SETOFF_SAME_HEAD = Citation(
    "F&O loss set off against speculative income (same head)", "s.70(1)", "s.108(1)",
    ACT_2025_URL)
SETOFF_INTER_HEAD = Citation(
    "Business loss against other heads (not salary); capital loss not against other heads",
    "s.71", "s.109", ACT_2025_URL)
SETOFF_ORDER = Citation(
    "Losses set off against slab-rate gains first, then the highest special rate, non-exempt "
    "before exemption-eligible LTCG", None, None, ACT_2025_URL,
    unverified=True, question="Q-008")
INTER_HEAD_AGAINST_CG = Citation(
    "Unabsorbed F&O loss set off against this year's capital gains", "s.71", "s.109",
    ACT_2025_URL, unverified=True, question="Q-010")
CARRY_CAPITAL = Citation(
    "Capital loss carried forward 8 years, same set-off restrictions", "s.74", "s.111",
    ACT_2025_URL)
CARRY_BUSINESS = Citation(
    "Business loss carried forward 8 years against business income", "s.72", "s.112",
    ACT_2025_URL)
CARRY_SPECULATIVE = Citation(
    "Speculation loss only against speculation income, carried forward 4 years", "s.73",
    "s.113", ACT_2025_URL)
RETURN_OF_LOSS = Citation(
    "Losses carry forward only if determined in a return filed on time", "s.80",
    "s.121, s.263(1)", ACT_2025_URL, unverified=True, question="Q-011")
LTCG_EXEMPTION = Citation(
    "LTCG on STT-paid equity up to ₹1,25,000 not taxed", "s.112A", "s.198(2)(a)",
    CBDT_CG_FAQ_URL)
BONUS_STRIPPING = Citation(
    "Loss on shares bought within 3 months before a bonus record date and sold within 9 months "
    "after it is ignored and added to the cost of the bonus shares still held",
    "s.94(8)", "s.175(9),(10)", ACT_2025_URL)
BONUS_STRIPPING_WINDOW = Citation(
    "Bonus-stripping 3-month / 9-month window boundaries assumed",
    "s.94(8)", "s.175(9)", ACT_2025_URL, unverified=True, question="Q-014")
ROUNDING = Citation(
    "Tax rounded to the nearest ₹10 (paise ignored, 5 and above rounds up)", "s.288B",
    "s.516", ACT_2025_URL)

COMMON = {
    c.topic: c
    for c in (
        HOLDING_PERIOD, HOLDING_BOUNDARY, FIFO, COMPUTATION, GRANDFATHERING,
        GRANDFATHERING_AFTER_SPLIT, EQUITY_FUND, SPECIFIED_FUND, UNLISTED_HOLDING, OTHER_LTCG,
        SLAB_STCG, FUND_CLASS, FUND_FIFO_PER_FOLIO, LISTED_FUND_UNITS,
        SPECIFIED_DEFINITION_1961, STCG_EQUITY, LTCG_EQUITY, LTCG_EXEMPTION,
        SETOFF_SAME_HEAD,
        STT_PAID_ASSUMED, SPECULATIVE, NON_SPECULATIVE, STT_BUSINESS_DEDUCTION, SETOFF_CAPITAL,
        SETOFF_INTER_HEAD, SETOFF_ORDER, INTER_HEAD_AGAINST_CG, CARRY_CAPITAL, CARRY_BUSINESS,
        CARRY_SPECULATIVE, RETURN_OF_LOSS, FIFO_PER_ACCOUNT, BONUS_STRIPPING,
        BONUS_STRIPPING_WINDOW,
        ROUNDING,
    )
}

BONUS_STRIPPING_1961 = Citation(
    BONUS_STRIPPING.topic, "s.94(8)", "s.175(9),(10)", ACT_2025_URL,
    unverified=True, question="Q-005")
"""For 1961 Act years: s.94(8)'s extension from units to securities (Finance Act 2022) is not
yet checked against an official 1961 text."""
