
# EDA Timing Report Analyzer & SDC Auto-Fix Generator

**Language:** Python 3 (no external dependencies beyond stdlib)  
**Platform:** Windows / Linux / Mac  
**Relevance:** NVIDIA CAD team · PSSG team · Physical Design

---

## What this project does

Parses Synopsys PrimeTime-style STA timing reports, classifies each path by severity, computes WNS/TNS/WHS, and auto-generates:
- `constraints.sdc` — Synopsys SDC with multicycle_path exceptions and set_min_delay fixes
- `fix_timing.tcl` — Synopsys TCL script with insert_buffer commands
- `timing_summary.json` — machine-readable JSON for dashboards

**Why it matters:** In a real chip design flow, identifying and scripting fixes for timing violations is done manually by Physical Design engineers. This tool automates the triage and first-pass fix generation step.

---

## Run immediately

```bash
python eda_timing_analyzer.py sample_reports/timing_report.rpt --design risc_v_core
```

No pip installs needed. Python 3.8+ only.

**Expected output:**
```
Total paths analyzed    6
Paths MET               2
Paths VIOLATED          4
  Setup violations      3
  Hold violations       1
WNS                     -3.780 ns  CRITICAL
TNS                     -4.522 ns
Timing Health Score     5/100
```

---

## File structure

```
eda_timing_analyzer/
  eda_timing_analyzer.py      ← Main script (all logic in one file)
  sample_reports/
    timing_report.rpt         ← Sample PrimeTime STA report (RISC-V core)
  output/                     ← Auto-created on first run
    constraints.sdc
    fix_timing.tcl
    timing_summary.json
  README.md
```

---

## Command-line options

```
python eda_timing_analyzer.py <report> [options]

Arguments:
  report              Path to STA report file
  --design NAME       Design name (default: risc_v_core)
  --clock NAME        Clock name for SDC (default: CLK)
  --period FLOAT      Clock period in ns (default: 5.0)
  --outdir DIR        Output directory (default: output/)
```

---

## Design decisions worth explaining in interview

**Why regex over an XML parser?**  
PrimeTime reports are plain text, not XML. Real EDA tools generate fixed-format text reports — regex is the industry-standard parsing approach for this.

**How does the SDC fix work?**  
For CRITICAL setup violations (slack < −1.0 ns), the tool generates `set_multicycle_path 2` — this tells the synthesis tool to allow the combinational logic 2 clock cycles to settle instead of 1, relaxing the timing constraint for that path.

**What is the Fmax computation?**  
`Fmax = 1 / (clock_period − |WNS|)` = 1 / (5.0 − 3.78) ≈ 820 MHz. This is the maximum frequency the design can reliably run at after fixing the critical path.

---

## Requirements

- Python 3.8 or higher
- No pip packages needed
- Works on Windows (VS Code terminal), Linux, Mac
