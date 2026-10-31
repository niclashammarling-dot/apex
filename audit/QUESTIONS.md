# APEX question map

The register the pyramid is drawn from (Niclas 2026-10-21: "Meningen är att den ska underhållas
automatiskt utifrån det arbete vi gör. Se det som en visuell representation av maskineriet i apex.
Inget fel med siffror, men de ska komma ur vårt arbete med att kartlägga hur vi får svar på alla
våra frågor").

One row per question: which question above it it answers, and which register rows answer it. The top
is O3 and the go criterion's seven groups (vault `raw/notes/2026-10/2026-10-16-apex-real-money-go-criterion-frame.md`,
group 1 ruled in `2026-10-19-apex-go-criterion-group1-and-signal-register-ruled.md`).

**Writing it is part of the work that asks a question.** A new S-row, gap row, CHECK or battery
cell names the question it answers here in the same commit. A row that answers no question is
reported as such: that is the check against gathering for its own sake.

**State is never written here.** It is read from the rows (derivation below). Hand-written text is
the questions themselves, and each go group's threshold status, which changes only by Niclas's ruling.

## References
- `S<n>`: a signal-register row (`backend/backtest/SIGNAL_REGISTER.md`, on master since 2026-10-21). State from
  the status column's first word: `open`, `adopted`, `discarded`.
- `GAP-<n>`: a numbered production-gap row (same file, § Live gate path). State from the parity
  column's first word: `yes` or `closed` (answered), `partial`, `no`, `off-both`, `engine-only`,
  `different`, `n/a`.
- `CHECK-<n>`: a row in `audit/CHECKS.md`. State from the nightly run: clean, or a finding at its
  severity. `CHECK-*` means every CHECK not named on another row.
- `Q-<name>`, `G<n>`: another question in this map.
- `PARAM-money`, `PARAM-ops`: every entry of `audit/PARAMETERS.json` in that tier. measured, structural
  and derived are answered; chosen is answered until its review date passes.
- `M<n>`: a row in § Missing rows below: the question needs a row that no register holds yet. Each
  one is a break in the chain, shown as such, until the row exists and the reference is replaced.

## Derivation (for `/api/ops/pyramid`, to build)
- A row is **answered** when it has reached a verdict: S `adopted`/`discarded`; GAP `yes`/`closed`/`n/a`;
  CHECK clean on its latest run (`audit/state/latest.json`: evaluated, no WARNING or CRITICAL
  finding); PARAM as above. An `M` row is never answered.
- A question is **answered** when every reference is answered, **open** when none is, **partial**
  otherwise. An `M` reference keeps it at most partial.
- A go group is scored only when its threshold is `set`.
- The pyramid shows per question the count of rows per state; a break is an open question.
- A test asserts that every reference resolves, every go group reaches at least one row, and every
  S-row and GAP row appears in this map.

## Top

| id | question | answers | threshold | answered by |
|---|---|---|---|---|
| O3 | Is APEX trustworthy enough for real money? | — | — | G1, G2, G3, G4, G5, G6, G7 |
| G1 | Edge: a measured edge in the battery, then shown live over max(3, n/rate) months at 90 % power | O3 | set (formula ruled 10-19; O3 direction: hit rate above 72 % on in-session entries) | Q-SIG, Q-EXE, Q-VER, S16, M1 |
| G2 | Risk: drawdown, worst day, losing streak and largest loss inside what the ported backtest predicts | O3 | not set | Q-PAR, Q-EXE |
| G3 | Robustness: positive without the best trades and the best sector; the 2022 fold; the parameters on a plateau | O3 | not set (plateau) | Q-PAR, S7, M2 |
| G4 | Live matches the model: live inside the ported backtest's distribution, slippage charged | O3 | scored after the port | Q-PAR, CHECK-89, CHECK-93 |
| G5 | Operations: clean sessions, no unresolved CRITICAL, go-live checklist closed, recovery paths induced | O3 | not set (clean sessions) | Q-OPS |
| G6 | Discipline: parameters frozen during the evaluation window | O3 | not set (window start) | CHECK-9, CHECK-13, PARAM-money, M3 |
| G7 | Staging: real money starts small; a stop rule is written before the first real order | O3 | partly set (stop rule ruled 2026-10-24: drawdown 6 % on realised equity; expectancy below the paper estimate's lower CI after 40 in-session trades; review 2026-11-21. Starting size not set) | Q-EXE, GAP-33, M4 |

## Layers

| id | question | answers | answered by |
|---|---|---|---|
| Q-VER | Can each verdict below be trusted: rule written before the run, controls passed, enough power? | G1, G2, G3 | CHECK-76, S9, S10, M1, M7 |
| Q-EXE | Do exits and sizing deliver the expected value per trade that group 1 rests on? | G1, G2, G7 | S15, GAP-4, GAP-24, GAP-24b, GAP-25, GAP-31, CHECK-31, CHECK-39, CHECK-46, CHECK-47, CHECK-92, CHECK-93, PARAM-money |
| Q-PAR | Can a backtest verdict be trusted for live? | G2, G3, G4 | GAP-1, GAP-2, GAP-3, GAP-5, GAP-6, GAP-7, GAP-10, GAP-11, GAP-12, GAP-13, GAP-14, GAP-15, GAP-16, GAP-17, GAP-18, GAP-19, GAP-20, GAP-21, GAP-22, GAP-22b, GAP-23, GAP-26, GAP-27, GAP-30, GAP-32, GAP-33, GAP-34, CHECK-7, CHECK-12, CHECK-25, CHECK-26, CHECK-54, CHECK-76, M6 |
| Q-SIG | Which inputs carry a measured direction edge? | G1 | S1, S2, S3, S4, S5, S6, S7, S8, S9, S10, S11, S12, S13, S14, S17, S18, S19, S20, S21, S22 |
| Q-DAT | Are the inputs there and true, live and in the tested period? | Q-SIG, Q-PAR | GAP-8, GAP-9, GAP-28, GAP-29, CHECK-10, CHECK-14, CHECK-15, CHECK-17, CHECK-22, CHECK-35, CHECK-55, CHECK-56, CHECK-75, CHECK-77, CHECK-78, CHECK-86, CHECK-87, CHECK-91, M5 |
| Q-OPS | Does the machinery run as built, every session? | G5 | CHECK-*, PARAM-ops |

## Missing rows

The summarising register holds what no other register does yet (Niclas 2026-10-21: "pyramiden måste
vara ett sammanfattande register … Det måste visa hur hela apex hänger ihop"). A row leaves this table
when its own register row exists; the reference above is replaced in the same commit.

| id | missing | needed by | where it would live |
|---|---|---|---|
| M1 | Battery verdict register: each pre-registered cell's verdict (the profit read's edge rule, R2 against RND; T-cards), readable by the repo | G1, Q-VER | the battery notes are in the vault only; 2026-10-24 added eight pre-registered cards (S20, S20b, S21, S21b, ATR exits, leg ablation, capped stop, L3+M4), verdicts in their vault cards with archived JSONs (`raw/notes/2026-10/data/2026-10-24-apex-*`); 2026-10-26: the overflow card (GAP-14's hand-set parameter, Q-EXE/G2) scoped for 2026-10-31 |
| M2 | Robustness card: without the best trades and sector, the 2022 fold, the parameter plateau | G3 | a card, then a register row; the time stop is the first plateau card (vault INDEX, filed 2026-10-27 for 2026-11-14: live's 25 days against a 15–40 grid; Niclas's spring hand backtests ~22; S14 follow-up `9ec1b6b`: 25 beats 15 and 10 in total P&L) |
| M3 | Freeze record: the evaluation window's start and the parameters frozen | G6 | Niclas's ruling on the window start |
| M4 | Stop rule: drawdown and expectancy limits, written before the first real order | G7 | ruled 2026-10-24 (G7 threshold; vault INDEX go-criterion line); running in live's L0 Portfolio since 2026-10-24 (`backend/gate/l0_portfolio.py`, PARAMETERS.json money entries `M4_*`, review 2026-11-21: cap level 4/6 %, interim 5 %). Backtest reading 2026-10-24 (L3+M4 card): the 6 % halt fires in every R1 history (realised drawdowns 11.7–16.4 %); level held until more evidence (Niclas 2026-10-24). 2026-10-27 (S14 follow-up `9ec1b6b`): the engine measures the halt and keeps trading, so L3 after the first crossing is not live's path; an enforced-M4 engine run is filed, blocked on Niclas's choice of stand-in reset rule (vault S14 card § 11.2). Live has no reset path (GAP-33): a Settings button with confirmation and an append-only reset record is filed for 2026-12-05 (Niclas 10-27: "Knappen är det manuella steget som är viktigt … ingen stress"). The engine card is unblocked: never-halt against time, market condition, shadow book and staged resets (Niclas 10-27: "alla 4"), filed for 2026-11-21, the M4 review date |
| M5 | Survivorship-free universe: delisted prices from 2015 (PRODUCTION_GAP prose, no numbered row) | Q-DAT | a PRODUCTION_GAP row; scope 2026-10-31 |
| M6 | The regime port merged into master (criterion ruled 10-19) | Q-PAR | done 2026-10-24 (`0964e7e`); leaves this table when Q-PAR's answered-by list is re-read |
| M7 | Calibrated L2 instrument: the episode-percentile CI false-passes at 6 % against a nominal 2.5 % (S14 R1 follow-up 2026-10-27, `9ec1b6b`); a null-distribution test runs at 3.0 %; alternatives (wild cluster bootstrap, bootstrap-t, CR2, calendar shift, episode sign test) in vault S14 card § 12.2, the wild bootstrap's calibration to be measured in the time-stop card | Q-VER | the card template and the next runner (S14 R2 card, vault INDEX 2026-11-14); S21b used the same CI, its unanswered reading stands |

## Rows that answer no question
None at the time of writing: every S-row S1–S22 and every GAP row 1–34 (with 22b, 24b) is named
above. S15 answers Q-EXE and S16 answers G1 directly. The test keeps it that way.
