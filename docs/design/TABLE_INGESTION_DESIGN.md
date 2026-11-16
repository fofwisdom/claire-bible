# Table Preservation and Table Budget Exemption

- **Status**: Implemented. Design principles only. Current behavior: [`INGESTION.md`](../implementation/INGESTION.md) §8.
- **Date**: 2026-08-27 · condensed 2026-11-16
- **Related**: [`PDF_INGESTION_AND_ADAPTIVE_EFFORT_DESIGN.md`](PDF_INGESTION_AND_ADAPTIVE_EFFORT_DESIGN.md)

## Goal / problem

Tables carry numbers, comparisons, and specifications. The old pipeline damaged them in three ways:

1. The HTML parser joined cell text into flat text. Rows and columns lost their relation.
2. The character budget counted table text. A long table pushed prose out, or a table at the end was cut in the middle. A cut table gives wrong relations.
3. The prompts did not tell the model to keep table data. The model left it out or shortened it.

## Decisions in force

1. **Keep tables as structured text at capture time.** An HTML table becomes a text table with its rows, columns, and caption. Claire writes AsciiDoc tables, not Markdown (RD24). Reason: the structure is the meaning of a table.
2. **Table text does not count against the prose budget.** Only prose uses the budget. Every table stays complete, in its original position. Reason: a partial table is worse than no table; prose can be shortened safely, a table cannot.
3. **A strict cut stays available as an option.** Reason: an operator can limit prompt size when tables are very large.
4. **Media inside tables may be removed.** Images, icons, and badges in cells break the table format. They are removed, and they are not image candidates for the detail text. Reason: the data matters, not the decoration.
5. **Prompts protect table data.** Extraction must take entities and relations from rows and columns. The detail text must rebuild tables and not merge or drop them.
6. **Old documents with tables can be found and processed again** without a full re-extraction of all documents. Reason: quota is limited; only affected documents need work.

## Rejected alternatives

- **Markdown tables.** Claire writes no Markdown (RD24). The first version used Markdown tables; they were replaced by AsciiDoc tables.
- **Flatten tables to sentences.** It loses the column relation and makes the text longer.

## Limits for later changes

- A new source type that can contain tables must give them in a form that the table detector finds, so that the exemption applies.
- The exemption has no upper limit for tables. If very large tables become a problem for the model context, add a separate table limit; do not count tables as prose again.
