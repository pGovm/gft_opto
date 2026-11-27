# GFT_Analysis_Tool — Project Report

This branch holds only the project report. The application's code, installers
and tests are on the `main` branch, and the released version is tagged
`v1.0.0`.

Do not merge this branch into `main`: it has none of the code, so a merge would
delete files there. To move the report to `main`, copy the files instead.

## Contents

| Path | What it is |
|---|---|
| `main_report.tex` | The report; includes each section in order |
| `report_setup.tex` | LaTeX packages and heading styles |
| `sections/` | One `.tex` file per section |
| `images/` | Figures used in the report |
| `main_report.pdf` | Last built copy of the report |

## Building the PDF

With a LaTeX distribution installed (TeX Live or MiKTeX), run from this folder:

```bash
latexmk -pdf main_report.tex
```

`latexmk` runs LaTeX as many times as needed to update the table of contents.
Without it, run `pdflatex main_report.tex` twice. LaTeX's working files
(`.aux`, `.log`, `.toc` and so on) are ignored by git. Commit the rebuilt
`main_report.pdf` when the report changes.
