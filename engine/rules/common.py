"""Citations shared by the rule packs. Section numbers for the 2025 Act are verified against
docs/sources/income-tax-act-2025-as-amended-by-fa-2026.pdf; 1961 Act numbers are not yet
checked against an official 1961 text (docs/OPEN_QUESTIONS.md Q-001)."""

from engine.rules.base import ACT_2025_URL, CBDT_CG_FAQ_URL, Citation

HOLDING_PERIOD = Citation(
    "Listed shares are short-term if held for not more than 12 months",
    "s.2(42A)", "s.2(101)(a),(b)", CBDT_CG_FAQ_URL)
FIFO = Citation("FIFO for demat securities", "s.45(2A)", "s.67(7)(c)", ACT_2025_URL)
COMPUTATION = Citation(
    "Gain = sale value - transfer expenses - cost; STT not deductible",
    "s.48", "s.72(1), s.72(3)(b)", ACT_2025_URL)
GRANDFATHERING = Citation(
    "Shares acquired before 1-Feb-2018: cost = higher of actual cost and lower of "
    "31-Jan-2018 FMV and sale value",
    "s.55(2)(ac)", "s.90(7), s.90(8)(b)", ACT_2025_URL)
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
SETOFF_INTER_HEAD = Citation(
    "Business loss against other heads (not salary); capital loss not against other heads",
    "s.71", "s.109", ACT_2025_URL)
SETOFF_ORDER = Citation(
    "Losses set off against the highest-rate gains first", None, None, ACT_2025_URL,
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
    "Losses carry forward only if the return is filed by the due date", "s.80", None,
    ACT_2025_URL, unverified=True, question="Q-011")
ROUNDING = Citation(
    "Tax rounded to the nearest ₹10 (paise ignored, 5 and above rounds up)", "s.288B",
    "s.516", ACT_2025_URL)

COMMON = {
    c.topic: c
    for c in (
        HOLDING_PERIOD, FIFO, COMPUTATION, GRANDFATHERING, STCG_EQUITY, LTCG_EQUITY,
        STT_PAID_ASSUMED, SPECULATIVE, NON_SPECULATIVE, STT_BUSINESS_DEDUCTION, SETOFF_CAPITAL,
        SETOFF_INTER_HEAD, SETOFF_ORDER, INTER_HEAD_AGAINST_CG, CARRY_CAPITAL, CARRY_BUSINESS,
        CARRY_SPECULATIVE, RETURN_OF_LOSS, ROUNDING,
    )
}
