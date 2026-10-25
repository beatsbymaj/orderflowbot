# 11. SIP sale conditions and venue codes for the feature engine

Checked 2026-10-25. Used by `src/flowforge/features/conditions.py`.

## Sources
- [U1] UTP Binary Output Specification v4.1 ("Revised – Trading Action Code", September 2026), Sale Condition Modifier table, Sale Condition Matrix, Market Center Originator ID and Sub-Market Center ID tables. https://www.utpplan.com/DOC/UtpBinaryOutputSpec.pdf (read 2026-10-25)
- [C1] CTS Pillar Output Specification v2.11b (2026-01-29), section 4.3 Participant IDs; regular sale code. https://www.ctaplan.com/publicdocs/ctaplan/CTS_Pillar_Output_Specification.pdf (linked from https://ctaplan.com/tech-specs; read 2026-10-25)
- [A1] Alpaca exchange-codes endpoint page (updated 2026-05-27) documents only the endpoint, not the table: https://docs.alpaca.markets/us/reference/stockmetaexchanges-1
- [A2] Alpaca staff on the forum (2024-08-22): Alpaca passes through SIP condition letters; no glossary of its own. https://forum.alpaca.markets/t/trade-condition-glossary/14867
- [P2] Phase 2 download (2026-10-25): SPY regular trades carry `" "`; codes seen were " ", I, F, 4, B, 7, V, T, C, O, Q.

## What the specs say
- **Regular sale**: CTA uses a space; its `@` code was retired in Pillar v1.2 [C1]. UTP still lists `@` [U1]. Alpaca shows `" "` for SPY (tape B) [P2]. Both are treated as regular.
- **UTP Sale Condition Matrix** [U1] (a "no" for any condition on a print wins):
  - update last, high/low and volume: @ A B D F K O S X Y 1 5 6 L
  - volume only: C H I R T U V W 7
  - high/low (not last) and volume: G P Z 4
  - M: market-center close, no consolidated volume; 9: corrected consolidated close, no volume; Q: market-center open, no volume
  - E, 8, N: placeholders, not defined
- **Codes** [U1]: F intermarket sweep, I odd lot, W average price, 4 derivatively priced, 7 qualified contingent trade, T Form T (extended hours), U extended hours out of sequence, Z sold out of sequence, O opening prints, 5 re-opening prints, 6 closing prints, X cross/periodic auction, M official close, Q official open.
- **Venue D** = FINRA on both tapes: CTA "FINRA Alternative Display Facility" [C1]; UTP "FINRA" with a sub-market id for the NYSE TRF (N), Nasdaq TRF Carteret (Q), Nasdaq TRF Chicago (B) or the ADF (blank) [U1]. So off-exchange share = volume with exchange `D`.

## How the features use them
| Class | Codes | Volume | Footprint row / OHLC / tick test | Aggressor side |
|---|---|---|---|---|
| No volume | M Q 9 | no | no | none |
| Off-market | C G H P R T U V W Z 4 7 | yes | no | none |
| Auction / cross | O 5 6 X | yes | yes | none |
| Odd lot | I | yes | yes (the SIP leaves odd lots out of "last", but they trade at the market) | classified |
| Everything else | " " @ F B ... | yes | yes | classified |

Unknown codes are counted in `day_stats.unknown_conditions` and treated as regular.

## UNVERIFIED (check on the PC)
- That Alpaca maps every TRF print to exchange `D` and doesn't expose the TRF id. The replay summary's off-exchange share should be near the market's ~40–50% TRF share if so.
- Whether Alpaca's trade `t` is the SIP or the participant timestamp. Classification uses the quote in force just before the trade's `t`; if `t` is the participant time and quotes use another clock, some trades get the wrong quote. Phase 4 can compare against IBKR tick-by-tick.
- CTA's own condition matrix (the Pillar spec text was cut off when read). Codes are shared with UTP, and the classes above follow the UTP matrix.
