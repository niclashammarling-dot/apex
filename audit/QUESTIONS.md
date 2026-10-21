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
- `MISSING: <what>`: the question needs a row that no register holds yet. Each one is a break in the
  chain, shown as such.

## Derivation (for `/api/ops/pyramid`, to build)
- A row is **answered** when it has reached a verdict: S `adopted`/`discarded`; GAP `yes`/`closed`/`n/a`;
  CHECK clean on its latest run.
- A question is **answered** when every reference is answered, **open** when none is, **partial**
  otherwise. A `MISSING` reference keeps it open.
- A go group is scored only when its threshold is `set`.
- The pyramid shows per question the count of rows per state; a break is an open question.
- A test asserts that every reference resolves, every go group reaches at least one row, and every
  S-row and GAP row appears in this map.

## Top

| id | question | answers | threshold | answered by |
|---|---|---|---|---|
| O3 | Is APEX trustworthy enough for real money? | — | — | G1, G2, G3, G4, G5, G6, G7 |
| G1 | Edge: a measured edge in the battery, then shown live over max(3, n/rate) months at 90 % power | O3 | set (formula ruled 10-19; O3 direction: hit rate above 72 % on in-session entries) | Q-SIG, Q-EXE, Q-VER, S16, MISSING: battery verdict register (the profit read's edge rule, R2 against RND, lives in vault notes only) |
| G2 | Risk: drawdown, worst day, losing streak and largest loss inside what the ported backtest predicts | O3 | not set | Q-PAR, Q-EXE |
| G3 | Robustness: positive without the best trades and the best sector; the 2022 fold; the parameters on a plateau | O3 | not set (plateau) | Q-PAR, S7, MISSING: robustness card |
| G4 | Live matches the model: live inside the ported backtest's distribution, slippage charged | O3 | scored after the port | Q-PAR, CHECK-89, CHECK-93 |
| G5 | Operations: clean sessions, no unresolved CRITICAL, go-live checklist closed, recovery paths induced | O3 | not set (clean sessions) | Q-OPS |
| G6 | Discipline: parameters frozen during the evaluation window | O3 | not set (window start) | CHECK-9, CHECK-13, MISSING: freeze record |
| G7 | Staging: real money starts small; a stop rule is written before the first real order | O3 | not set | Q-EXE, MISSING: stop rule |

## Layers

| id | question | answers | answered by |
|---|---|---|---|
| Q-VER | Can each verdict below be trusted: rule written before the run, controls passed, enough power? | G1, G2, G3 | CHECK-76, S9, S10, MISSING: battery verdict register |
| Q-EXE | Do exits and sizing deliver the expected value per trade that group 1 rests on? | G1, G2, G7 | S15, GAP-4, GAP-24, GAP-24b, GAP-25, GAP-31, CHECK-31, CHECK-39, CHECK-46, CHECK-47, CHECK-92, CHECK-93 |
| Q-PAR | Can a backtest verdict be trusted for live? | G2, G3, G4 | GAP-1, GAP-2, GAP-3, GAP-5, GAP-6, GAP-7, GAP-10, GAP-11, GAP-12, GAP-13, GAP-14, GAP-15, GAP-16, GAP-17, GAP-18, GAP-19, GAP-20, GAP-21, GAP-22, GAP-22b, GAP-23, GAP-26, GAP-27, GAP-30, GAP-32, CHECK-7, CHECK-12, CHECK-25, CHECK-26, CHECK-54, CHECK-76, MISSING: regime port merged |
| Q-SIG | Which inputs carry a measured direction edge? | G1 | S1, S2, S3, S4, S5, S6, S7, S8, S9, S10, S11, S12, S13, S14 |
| Q-DAT | Are the inputs there and true, live and in the tested period? | Q-SIG, Q-PAR | GAP-8, GAP-9, GAP-28, GAP-29, CHECK-10, CHECK-14, CHECK-15, CHECK-17, CHECK-22, CHECK-35, CHECK-55, CHECK-56, CHECK-75, CHECK-77, CHECK-78, CHECK-86, CHECK-87, CHECK-91, MISSING: survivorship-free universe (PRODUCTION_GAP prose line 99, no row; scope 2026-10-31) |
| Q-OPS | Does the machinery run as built, every session? | G5 | CHECK-* |

## Rows that answer no question
None at the time of writing: every S-row S1–S16 and every GAP row 1–32 (with 22b, 24b) is named
above. S15 answers Q-EXE and S16 answers G1 directly. The test keeps it that way.
