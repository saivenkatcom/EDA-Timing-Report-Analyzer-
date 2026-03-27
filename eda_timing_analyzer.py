"""
=============================================================
  EDA Timing Report Analyzer & SDC/TCL Auto-Fix Generator
=============================================================
  Author  : [Your Name] | B.Tech ECE Final Year
  Purpose : Parse Synopsys PrimeTime STA reports, classify
            violations by severity, and auto-generate SDC
            constraints and Synopsys TCL fix scripts.
  Tools   : Pure Python 3 — no EDA tools needed to run.
            Targets NVIDIA CAD / PSSG / Physical Design teams.
=============================================================
"""

import re
import os
import sys
import json
import argparse
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime


# ─────────────────────────────────────────────────────────────
#  DATA CLASSES
# ─────────────────────────────────────────────────────────────

@dataclass
class TimingPath:
    """Represents one timing path parsed from an STA report."""
    path_id:    int
    startpoint: str
    endpoint:   str
    path_type:  str          # "max" (setup) or "min" (hold)
    slack:      float
    status:     str          # "MET" or "VIOLATED"
    violation_type: str      # "SETUP", "HOLD", or "NONE"
    arrival_time:   float
    required_time:  float
    logic_cells:    List[str] = field(default_factory=list)

    @property
    def severity(self) -> str:
        """Classify violation severity by slack value."""
        if self.status == "MET":
            return "CLEAN"
        if self.slack <= -1.0:
            return "CRITICAL"
        if self.slack <= -0.3:
            return "MAJOR"
        return "MINOR"

    @property
    def wns_contribution(self) -> float:
        """Return magnitude of violation (positive = worse)."""
        return abs(self.slack) if self.status == "VIOLATED" else 0.0


@dataclass
class DesignSummary:
    """Aggregated statistics across all timing paths."""
    total_paths:     int = 0
    violated_paths:  int = 0
    met_paths:       int = 0
    setup_violations: int = 0
    hold_violations:  int = 0
    critical_paths:  int = 0
    major_paths:     int = 0
    minor_paths:     int = 0
    wns:             float = 0.0   # Worst Negative Slack
    tns:             float = 0.0   # Total Negative Slack
    whs:             float = 0.0   # Worst Hold Slack (negative = violated)


# ─────────────────────────────────────────────────────────────
#  PARSER
# ─────────────────────────────────────────────────────────────

class STAReportParser:
    """
    Parses Synopsys PrimeTime-style STA timing reports.
    Handles both setup (max delay) and hold (min delay) paths.
    """

    # Regex patterns for each line of interest
    RE_STARTPOINT   = re.compile(r"Startpoint:\s+(\S+)")
    RE_ENDPOINT     = re.compile(r"Endpoint:\s+(\S+)")
    RE_PATH_TYPE    = re.compile(r"Path Type:\s+(\w+)")
    RE_SLACK_MET    = re.compile(r"slack\s+\(MET\)\s+([\d.]+)")
    RE_SLACK_VIOL   = re.compile(r"slack\s+\(VIOLATED\)\s+(-[\d.]+)")
    RE_ARRIVAL      = re.compile(r"data arrival time\s+([\d.]+)$")
    RE_REQUIRED     = re.compile(r"data required time\s+([\d.]+)$")
    RE_CELL         = re.compile(r"(U\d+)/Z\s+\((\w+)\)")

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.paths: List[TimingPath] = []
        self._path_counter = 0

    def parse(self) -> List[TimingPath]:
        """Read the report file and extract all timing paths."""
        if not os.path.exists(self.filepath):
            raise FileNotFoundError(f"Report not found: {self.filepath}")

        with open(self.filepath, "r") as f:
            content = f.read()

        # Split by path separator blocks
        raw_paths = re.split(r"#{3,}.*?#{3,}\n#.*Path \d+.*\n#{3,}", content,
                             flags=re.DOTALL)

        # Re-parse properly by scanning line-by-line
        self.paths = self._parse_line_by_line(content)
        return self.paths

    def _parse_line_by_line(self, content: str) -> List[TimingPath]:
        """State-machine parser that extracts each path."""
        paths = []
        lines = content.split("\n")

        # Parser state
        in_path   = False
        startpt   = None
        endpt     = None
        path_type = None
        slack     = None
        status    = None
        arrival   = None
        required  = None
        cells     = []

        for line in lines:
            line_s = line.strip()

            # New path block start
            if re.match(r"\s*#\s*={3,}", line) or ("# Path" in line and "(VIOLATED" in line) or ("# Path" in line and "(MET)" in line):
                # Save previous path if complete
                if startpt and endpt and slack is not None:
                    paths.append(self._build_path(
                        startpt, endpt, path_type, slack,
                        status, arrival, required, cells
                    ))
                # Reset state
                in_path   = True
                startpt   = None
                endpt     = None
                path_type = None
                slack     = None
                status    = None
                arrival   = None
                required  = None
                cells     = []
                continue

            if not in_path:
                continue

            m = self.RE_STARTPOINT.search(line_s)
            if m:
                startpt = m.group(1)
                continue

            m = self.RE_ENDPOINT.search(line_s)
            if m:
                endpt = m.group(1)
                continue

            m = self.RE_PATH_TYPE.search(line_s)
            if m:
                path_type = m.group(1)
                continue

            m = self.RE_SLACK_MET.search(line_s)
            if m:
                slack  = float(m.group(1))
                status = "MET"
                continue

            m = self.RE_SLACK_VIOL.search(line_s)
            if m:
                slack  = float(m.group(1))
                status = "VIOLATED"
                continue

            # Arrival / required (last occurrence wins)
            m = self.RE_ARRIVAL.match(line_s)
            if m:
                arrival = float(m.group(1))
                continue

            m = self.RE_REQUIRED.match(line_s)
            if m:
                required = float(m.group(1))
                continue

            # Collect cell names on critical path
            m = self.RE_CELL.search(line_s)
            if m:
                cells.append(f"{m.group(1)}({m.group(2)})")

        # Save last path
        if startpt and endpt and slack is not None:
            paths.append(self._build_path(
                startpt, endpt, path_type, slack,
                status, arrival, required, cells
            ))

        return paths

    def _build_path(self, startpt, endpt, path_type, slack,
                    status, arrival, required, cells) -> TimingPath:
        self._path_counter += 1
        pt = path_type or "max"
        vtype = ("SETUP" if pt == "max" and status == "VIOLATED"
                 else "HOLD" if pt == "min" and status == "VIOLATED"
                 else "NONE")
        return TimingPath(
            path_id       = self._path_counter,
            startpoint    = startpt or "unknown",
            endpoint      = endpt or "unknown",
            path_type     = pt,
            slack         = slack,
            status        = status or "MET",
            violation_type= vtype,
            arrival_time  = arrival or 0.0,
            required_time = required or 0.0,
            logic_cells   = cells[:],
        )


# ─────────────────────────────────────────────────────────────
#  ANALYZER
# ─────────────────────────────────────────────────────────────

class TimingAnalyzer:
    """
    Analyzes parsed timing paths and computes WNS, TNS,
    violation breakdown, and per-path severity scores.
    """

    def __init__(self, paths: List[TimingPath]):
        self.paths = paths
        self.summary = DesignSummary()
        self._compute_summary()

    def _compute_summary(self):
        s = self.summary
        s.total_paths = len(self.paths)

        violated = [p for p in self.paths if p.status == "VIOLATED"]
        met      = [p for p in self.paths if p.status == "MET"]

        s.violated_paths  = len(violated)
        s.met_paths       = len(met)
        s.setup_violations = sum(1 for p in violated if p.violation_type == "SETUP")
        s.hold_violations  = sum(1 for p in violated if p.violation_type == "HOLD")

        s.critical_paths = sum(1 for p in violated if p.severity == "CRITICAL")
        s.major_paths    = sum(1 for p in violated if p.severity == "MAJOR")
        s.minor_paths    = sum(1 for p in violated if p.severity == "MINOR")

        if violated:
            s.wns = min(p.slack for p in violated if p.violation_type == "SETUP") \
                    if any(p.violation_type == "SETUP" for p in violated) else 0.0
            s.tns = sum(p.slack for p in violated if p.violation_type == "SETUP")
            s.whs = min(p.slack for p in violated if p.violation_type == "HOLD") \
                    if any(p.violation_type == "HOLD" for p in violated) else 0.0

    def worst_paths(self, n: int = 5) -> List[TimingPath]:
        """Return n most violated paths sorted by slack."""
        viol = [p for p in self.paths if p.status == "VIOLATED"]
        return sorted(viol, key=lambda p: p.slack)[:n]

    def get_timing_health_score(self) -> int:
        """
        Score from 0–100 indicating timing closure quality.
        Used to quickly classify design readiness.
        """
        if self.summary.total_paths == 0:
            return 0
        ratio   = self.summary.met_paths / self.summary.total_paths
        penalty = abs(self.summary.wns) * 5 + abs(self.summary.tns) * 2
        score   = max(0, int(ratio * 100 - penalty))
        return min(score, 100)


# ─────────────────────────────────────────────────────────────
#  REPORT GENERATOR
# ─────────────────────────────────────────────────────────────

class ReportGenerator:
    """Prints a formatted analysis report to the console."""

    SEV_COLOR = {
        "CRITICAL": "\033[91m",   # Red
        "MAJOR":    "\033[93m",   # Yellow
        "MINOR":    "\033[96m",   # Cyan
        "CLEAN":    "\033[92m",   # Green
    }
    RESET = "\033[0m"

    def __init__(self, analyzer: TimingAnalyzer, design_name: str = "risc_v_core"):
        self.a = analyzer
        self.design = design_name

    def _color(self, text: str, sev: str) -> str:
        c = self.SEV_COLOR.get(sev, "")
        return f"{c}{text}{self.RESET}"

    def print_report(self):
        s = self.a.summary
        score = self.a.get_timing_health_score()

        print("\n" + "=" * 62)
        print(f"  EDA TIMING ANALYSIS REPORT")
        print(f"  Design  : {self.design}")
        print(f"  Date    : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 62)

        # ── Summary block ──────────────────────────────────────
        print("\n  SUMMARY")
        print(f"  {'Total paths analyzed':<32} {s.total_paths}")
        print(f"  {'Paths MET':<32} {self._color(str(s.met_paths), 'CLEAN')}")
        print(f"  {'Paths VIOLATED':<32} {self._color(str(s.violated_paths), 'CRITICAL' if s.violated_paths else 'CLEAN')}")
        print(f"  {'  └── Setup violations':<32} {s.setup_violations}")
        print(f"  {'  └── Hold violations':<32} {s.hold_violations}")
        print()
        print(f"  {'Worst Negative Slack (WNS)':<32} {self._color(f'{s.wns:.3f} ns', 'CRITICAL' if s.wns < -1 else 'MAJOR')}")
        print(f"  {'Total Negative Slack (TNS)':<32} {s.tns:.3f} ns")
        print(f"  {'Worst Hold Slack (WHS)':<32} {s.whs:.3f} ns")
        print()
        print(f"  {'Timing Health Score':<32} {score}/100  {'✅ GOOD' if score >= 80 else '⚠️  NEEDS FIXING' if score >= 50 else '🔴 CRITICAL'}")

        # ── Severity breakdown ─────────────────────────────────
        print("\n  VIOLATION BREAKDOWN BY SEVERITY")
        print(f"  {self._color('● CRITICAL (slack ≤ -1.0 ns)', 'CRITICAL'):<50} {s.critical_paths} path(s)")
        print(f"  {self._color('● MAJOR    (slack -0.3 to -1.0 ns)', 'MAJOR'):<50} {s.major_paths} path(s)")
        print(f"  {self._color('● MINOR    (slack 0 to -0.3 ns)', 'MINOR'):<50} {s.minor_paths} path(s)")

        # ── Per-path details ───────────────────────────────────
        print("\n  ALL PATHS")
        print(f"  {'ID':<4} {'Startpoint':<22} {'Endpoint':<22} {'Type':<6} {'Slack':>8}  {'Severity'}")
        print("  " + "-" * 76)
        for p in sorted(self.a.paths, key=lambda x: x.slack):
            sev = p.severity
            slack_str = f"{p.slack:+.3f} ns"
            print(f"  {p.path_id:<4} {p.startpoint:<22} {p.endpoint:<22} "
                  f"{p.violation_type:<6} {slack_str:>10}  "
                  f"{self._color(sev, sev)}")

        # ── Worst paths ────────────────────────────────────────
        worst = self.a.worst_paths(3)
        if worst:
            print("\n  TOP 3 WORST VIOLATING PATHS")
            for i, p in enumerate(worst, 1):
                print(f"\n  [{i}] Path {p.path_id} — {self._color(p.severity, p.severity)}")
                print(f"      From : {p.startpoint}")
                print(f"      To   : {p.endpoint}")
                print(f"      Slack: {p.slack:.3f} ns  |  Type: {p.violation_type}")
                if p.logic_cells:
                    print(f"      Cells on path: {', '.join(p.logic_cells[:4])}")

        print("\n" + "=" * 62)


# ─────────────────────────────────────────────────────────────
#  SDC AUTO-FIX GENERATOR
# ─────────────────────────────────────────────────────────────

class SDCGenerator:
    """
    Auto-generates SDC constraint files and Synopsys TCL
    fix scripts based on detected violations.

    Fix strategies used:
      SETUP violations → set_multicycle_path, optimize_registers
      HOLD  violations → set_min_delay, insert_buffer suggestion
    """

    def __init__(self, analyzer: TimingAnalyzer, design_name: str,
                 clock_name: str = "CLK", clock_period: float = 5.0):
        self.a           = analyzer
        self.design      = design_name
        self.clock_name  = clock_name
        self.clock_period= clock_period

    # ── SDC file ────────────────────────────────────────────────

    def generate_sdc(self, outpath: str = "output/constraints.sdc"):
        os.makedirs(os.path.dirname(outpath), exist_ok=True)
        lines = []
        lines.append(f"#{'='*60}")
        lines.append(f"# Auto-generated SDC — design: {self.design}")
        lines.append(f"# Generated by eda_timing_analyzer.py")
        lines.append(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"#{'='*60}\n")

        # Primary clock
        lines.append("# ── Primary clock constraint ──────────────────────────")
        lines.append(f"create_clock -name {self.clock_name} \\")
        lines.append(f"             -period {self.clock_period} \\")
        lines.append(f"             -waveform {{0 {self.clock_period/2}}} \\")
        lines.append(f"             [get_ports {self.clock_name}]\n")

        # Clock uncertainty
        lines.append("# ── Clock uncertainty (skew + jitter) ─────────────────")
        lines.append(f"set_clock_uncertainty -setup 0.1 [get_clocks {self.clock_name}]")
        lines.append(f"set_clock_uncertainty -hold  0.05 [get_clocks {self.clock_name}]\n")

        # Input/output delays
        lines.append("# ── I/O delays (assumed 30% of clock period) ──────────")
        io_delay = round(self.clock_period * 0.30, 2)
        lines.append(f"set_input_delay  -max {io_delay} -clock {self.clock_name} [all_inputs]")
        lines.append(f"set_output_delay -max {io_delay} -clock {self.clock_name} [all_outputs]\n")

        # Multicycle paths for critical setup violations
        setup_viols = [p for p in self.a.paths
                       if p.violation_type == "SETUP" and p.severity == "CRITICAL"]
        if setup_viols:
            lines.append("# ── Multicycle path exceptions (critical setup paths) ──")
            lines.append("# These paths have combinational logic too deep for 1 cycle.")
            lines.append("# Relaxing to 2-cycle allows the logic to settle properly.")
            for p in setup_viols:
                lines.append(f"set_multicycle_path 2 -setup \\")
                lines.append(f"    -from [get_cells {{{p.startpoint}}}] \\")
                lines.append(f"    -to   [get_cells {{{p.endpoint}}}]")
            lines.append("")

        # Min delay for hold violations
        hold_viols = [p for p in self.a.paths if p.violation_type == "HOLD"]
        if hold_viols:
            lines.append("# ── Hold time fixes (min delay constraints) ────────────")
            lines.append("# Increasing min delay forces tool to insert buffers/delays.")
            for p in hold_viols:
                fix_delay = round(abs(p.slack) + 0.1, 3)
                lines.append(f"set_min_delay {fix_delay} \\")
                lines.append(f"    -from [get_cells {{{p.startpoint}}}] \\")
                lines.append(f"    -to   [get_cells {{{p.endpoint}}}]")
            lines.append("")

        # Disable timing on false paths (example)
        lines.append("# ── False paths (scan / test mode — example) ───────────")
        lines.append("# set_false_path -from [get_ports scan_in]")
        lines.append("# set_false_path -to   [get_ports scan_out]\n")

        with open(outpath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"\n  ✅  SDC written  → {outpath}")

    # ── TCL fix script ──────────────────────────────────────────

    def generate_tcl(self, outpath: str = "output/fix_timing.tcl"):
        os.makedirs(os.path.dirname(outpath), exist_ok=True)
        lines = []
        lines.append(f"#{'='*60}")
        lines.append(f"# Synopsys PT / DC Fix Script — design: {self.design}")
        lines.append(f"# Generated by eda_timing_analyzer.py")
        lines.append(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"#{'='*60}\n")

        lines.append("# ── Load design ────────────────────────────────────────")
        lines.append(f"read_verilog  {self.design}.v")
        lines.append(f"read_sdc      constraints.sdc")
        lines.append(f"link_design   {self.design}\n")

        lines.append("# ── Compile (initial optimization pass) ────────────────")
        lines.append("compile_ultra -no_autoungroup\n")

        # Setup fix commands
        setup_viols = [p for p in self.a.paths
                       if p.violation_type == "SETUP" and p.severity in ("CRITICAL", "MAJOR")]
        if setup_viols:
            lines.append("# ── Setup violation fixes ───────────────────────────────")
            lines.append("# Strategy: restructure combinational logic, upsize cells,")
            lines.append("#           or relax via multicycle path exception.")
            lines.append("")
            for p in setup_viols:
                magnitude = abs(p.slack)
                lines.append(f"# Path {p.path_id}: slack={p.slack:.3f}ns  ({p.severity})")
                lines.append(f"#   From: {p.startpoint}  →  To: {p.endpoint}")
                if magnitude > 1.0:
                    lines.append(f"set_multicycle_path 2 -setup \\")
                    lines.append(f"    -from [get_cells {{{p.startpoint}}}] \\")
                    lines.append(f"    -to   [get_cells {{{p.endpoint}}}]")
                else:
                    lines.append(f"# Upsize critical cells — run optimise_registers")
                    lines.append(f"optimize_registers -path_group {self.clock_name} \\")
                    lines.append(f"    -effort high")
                lines.append("")

        # Hold fix commands
        hold_viols = [p for p in self.a.paths if p.violation_type == "HOLD"]
        if hold_viols:
            lines.append("# ── Hold violation fixes ────────────────────────────────")
            lines.append("# Strategy: insert delay buffers on short paths.")
            lines.append("")
            for p in hold_viols:
                lines.append(f"# Path {p.path_id}: hold slack={p.slack:.3f}ns")
                lines.append(f"#   From: {p.startpoint}  →  To: {p.endpoint}")
                buf_count = max(1, int(abs(p.slack) / 0.05))
                lines.append(f"# → Insert ~{buf_count} BUFX2 buffer(s) on this path")
                lines.append(f"insert_buffer -path_group {self.clock_name} \\")
                lines.append(f"    -from [get_cells {{{p.startpoint}}}] \\")
                lines.append(f"    -to   [get_cells {{{p.endpoint}}}] \\")
                lines.append(f"    -buffer_list {{BUFX2 BUFX4}} \\")
                lines.append(f"    -num_buffers {buf_count}")
                lines.append("")

        lines.append("# ── Re-run STA after fixes ──────────────────────────────")
        lines.append("report_timing -max_paths 20 -path_type full > timing_post_fix.rpt")
        lines.append("report_constraint -all_violators > violations_post_fix.rpt\n")

        lines.append("# ── Write final netlist ─────────────────────────────────")
        lines.append(f"write -format verilog -output {self.design}_fixed.v")
        lines.append(f"write_sdc             {self.design}_fixed.sdc")
        lines.append(f"write_parasitics      -format spef {self.design}_fixed.spef")

        with open(outpath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"  ✅  TCL script   → {outpath}")

    # ── JSON summary ────────────────────────────────────────────

    def export_json(self, outpath: str = "output/timing_summary.json"):
        os.makedirs(os.path.dirname(outpath), exist_ok=True)
        s = self.a.summary
        data = {
            "design": self.design,
            "generated": datetime.now().isoformat(),
            "summary": {
                "total_paths":      s.total_paths,
                "violated_paths":   s.violated_paths,
                "met_paths":        s.met_paths,
                "setup_violations": s.setup_violations,
                "hold_violations":  s.hold_violations,
                "wns_ns":           s.wns,
                "tns_ns":           s.tns,
                "whs_ns":           s.whs,
                "timing_health_score": self.a.get_timing_health_score()
            },
            "violations": [
                {
                    "path_id":    p.path_id,
                    "startpoint": p.startpoint,
                    "endpoint":   p.endpoint,
                    "type":       p.violation_type,
                    "slack_ns":   p.slack,
                    "severity":   p.severity
                }
                for p in self.a.paths if p.status == "VIOLATED"
            ]
        }
        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"  ✅  JSON summary → {outpath}")


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="EDA Timing Report Analyzer — parses PrimeTime STA "
                    "reports and auto-generates SDC/TCL fix scripts."
    )
    parser.add_argument("report",
                        help="Path to the STA timing report file (.rpt)")
    parser.add_argument("--design",    default="risc_v_core",
                        help="Design name (default: risc_v_core)")
    parser.add_argument("--clock",     default="CLK",
                        help="Primary clock name (default: CLK)")
    parser.add_argument("--period",    type=float, default=5.0,
                        help="Clock period in ns (default: 5.0)")
    parser.add_argument("--outdir",    default="output",
                        help="Output directory (default: ./output)")
    args = parser.parse_args()

    print("\n  ◆  EDA Timing Analyzer — starting ...")
    print(f"  ◆  Reading report : {args.report}")

    # 1. Parse
    parser_obj = STAReportParser(args.report)
    paths = parser_obj.parse()
    print(f"  ◆  Parsed {len(paths)} timing paths\n")

    # 2. Analyze
    analyzer = TimingAnalyzer(paths)

    # 3. Print report
    reporter = ReportGenerator(analyzer, args.design)
    reporter.print_report()

    # 4. Generate outputs
    print("\n  GENERATING FIX ARTIFACTS ...")
    gen = SDCGenerator(analyzer, args.design, args.clock, args.period)
    gen.generate_sdc(f"{args.outdir}/constraints.sdc")
    gen.generate_tcl(f"{args.outdir}/fix_timing.tcl")
    gen.export_json(f"{args.outdir}/timing_summary.json")

    print(f"\n  All files written to: ./{args.outdir}/")
    print("  ─────────────────────────────────────────────────")
    print("  Done. Review output/ folder and push to GitHub.\n")


if __name__ == "__main__":
    main()
