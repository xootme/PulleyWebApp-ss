"""
generate_checkin_report.py
--------------------------
Generates an HTML report card for the current deploy and saves it to
checkins/<date>_<short-hash>.html

Run after every deploy (after git push):
    python generate_checkin_report.py

Reads:
  - git log / diff for commit info and changed files
  - Perf_History.csv for benchmark comparison (latest vs previous commit)
  - logs/bug_reports.log for bugs closed in this session (optional)
"""

import csv
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
PERF_CSV = os.path.join(ROOT, 'Perf_History.csv')
CHECKINS_DIR = os.path.join(ROOT, 'checkins')


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(*args):
    result = subprocess.run(['git'] + list(args), capture_output=True, text=True, cwd=ROOT)
    return result.stdout.strip()


def get_commit_info():
    short_hash = _git('rev-parse', '--short', 'HEAD')
    full_hash  = _git('rev-parse', 'HEAD')
    subject    = _git('log', '-1', '--format=%s')
    body       = _git('log', '-1', '--format=%b')
    author     = _git('log', '-1', '--format=%an')
    date_str   = _git('log', '-1', '--format=%ci')
    return {
        'short_hash': short_hash,
        'full_hash':  full_hash,
        'subject':    subject,
        'body':       body.strip(),
        'author':     author,
        'date':       date_str,
    }


def get_changed_files():
    """Files changed in the most recent commit vs its parent."""
    raw = _git('diff', '--stat', 'HEAD~1', 'HEAD')
    lines = raw.splitlines()
    files = []
    for line in lines:
        m = re.match(r'^\s+(\S.*?)\s*\|', line)
        if m:
            files.append(m.group(1).strip())
    summary = lines[-1] if lines else ''
    return files, summary


def get_commit_log(n=10):
    """Last n commits as list of (hash, subject)."""
    raw = _git('log', f'-{n}', '--format=%h|%s')
    entries = []
    for line in raw.splitlines():
        parts = line.split('|', 1)
        if len(parts) == 2:
            entries.append(parts)
    return entries


# ---------------------------------------------------------------------------
# Benchmark helpers
# ---------------------------------------------------------------------------

def load_perf_csv():
    if not os.path.exists(PERF_CSV):
        return []
    with open(PERF_CSV, newline='') as f:
        return list(csv.DictReader(f))


def get_commits_in_csv(rows):
    """Ordered list of distinct commits (oldest first) present in CSV."""
    seen = []
    for r in rows:
        if r['commit'] not in seen:
            seen.append(r['commit'])
    return seen


def perf_by_commit(rows):
    """Return {commit: {test: mean_ms}}."""
    data = defaultdict(dict)
    for r in rows:
        try:
            data[r['commit']][r['test']] = float(r['mean_ms'])
        except (KeyError, ValueError):
            pass
    return data


def benchmark_comparison(rows):
    """Compare latest commit's benchmarks to the previous commit in the CSV.

    Returns list of dicts with keys: test, current_ms, prev_ms, delta_pct, flag
    """
    commits = get_commits_in_csv(rows)
    if len(commits) < 2:
        return [], None, None
    latest_commit = commits[-1]
    prev_commit   = commits[-2]
    by_commit     = perf_by_commit(rows)
    latest = by_commit[latest_commit]
    prev   = by_commit[prev_commit]
    results = []
    for test in sorted(set(latest) | set(prev)):
        cur = latest.get(test)
        prv = prev.get(test)
        if cur is None or prv is None:
            delta_pct = None
            flag = 'new' if prv is None else 'removed'
        else:
            delta_pct = (cur - prv) / prv * 100.0
            if delta_pct > 50:
                flag = 'regression'
            elif delta_pct > 20:
                flag = 'slow'
            elif delta_pct < -20:
                flag = 'faster'
            else:
                flag = 'ok'
        results.append({
            'test':       test,
            'current_ms': cur,
            'prev_ms':    prv,
            'delta_pct':  delta_pct,
            'flag':       flag,
        })
    return results, latest_commit, prev_commit


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

FLAG_STYLE = {
    'ok':        ('✓',  '#16a34a', '#f0fdf4'),
    'faster':    ('⬆',  '#0369a1', '#eff6ff'),
    'slow':      ('⚠',  '#b45309', '#fffbeb'),
    'regression':('✗',  '#dc2626', '#fef2f2'),
    'new':       ('+',  '#7c3aed', '#faf5ff'),
    'removed':   ('–',  '#6b7280', '#f9fafb'),
}

def _fmt_ms(v):
    if v is None:
        return '—'
    return f'{v:.1f}'

def _fmt_delta(v):
    if v is None:
        return '—'
    sign = '+' if v >= 0 else ''
    return f'{sign}{v:.1f}%'


def generate_html(commit_info, changed_files, diff_summary, recent_log,
                  bench_rows, latest_commit, prev_commit):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    short = commit_info['short_hash']
    subject = commit_info['subject']
    body = commit_info['body']
    date = commit_info['date']

    # Changed files table
    files_html = ''
    for f in changed_files:
        files_html += f'<tr><td class="mono">{f}</td></tr>\n'
    if not files_html:
        files_html = '<tr><td class="muted">No file diff available</td></tr>'

    # Benchmark table
    bench_html = ''
    has_regressions = False
    for row in bench_rows:
        flag  = row['flag']
        icon, color, bg = FLAG_STYLE.get(flag, ('?', '#000', '#fff'))
        delta_str = _fmt_delta(row['delta_pct'])
        cur_str   = _fmt_ms(row['current_ms'])
        prv_str   = _fmt_ms(row['prev_ms'])
        if flag == 'regression':
            has_regressions = True
        bench_html += (
            f'<tr style="background:{bg}">'
            f'<td class="mono">{row["test"]}</td>'
            f'<td class="num">{prv_str}</td>'
            f'<td class="num">{cur_str}</td>'
            f'<td class="num" style="color:{color};font-weight:600">{delta_str}</td>'
            f'<td style="color:{color};text-align:center">{icon}</td>'
            f'</tr>\n'
        )
    if not bench_html:
        bench_html = '<tr><td colspan="5" class="muted">No benchmark data</td></tr>'

    perf_header = ''
    if prev_commit and latest_commit:
        perf_header = f'Comparing <code>{latest_commit[:7]}</code> vs <code>{prev_commit[:7]}</code>'
    regression_banner = ''
    if has_regressions:
        regression_banner = (
            '<div style="background:#fef2f2;border:2px solid #dc2626;border-radius:6px;'
            'padding:10px 16px;margin-bottom:16px;color:#dc2626;font-weight:600;">'
            '⚠ One or more benchmarks regressed &gt;50% — investigate before shipping.'
            '</div>'
        )

    # Recent commits
    log_html = ''
    for h, s in recent_log:
        marker = ' style="font-weight:700"' if h == short else ''
        log_html += f'<tr{marker}><td class="mono">{h}</td><td>{s}</td></tr>\n'

    body_section = ''
    if body:
        body_section = f'<p class="body-text">{body}</p>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Deploy Report — {short} — {now}</title>
<style>
  body {{ font-family: system-ui, sans-serif; font-size: 14px; color: #1e293b;
         margin: 0; padding: 24px; background: #f8fafc; }}
  .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
           padding: 20px 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  h1 {{ margin: 0 0 4px; font-size: 20px; }}
  h2 {{ font-size: 14px; font-weight: 600; text-transform: uppercase;
        letter-spacing: .06em; color: #64748b; margin: 0 0 12px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; font-size: 12px; color: #64748b; border-bottom: 1px solid #e2e8f0;
        padding: 4px 8px; }}
  td {{ padding: 4px 8px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }}
  .mono {{ font-family: ui-monospace, monospace; font-size: 12px; }}
  .num  {{ text-align: right; font-family: ui-monospace, monospace; font-size: 12px; }}
  .muted {{ color: #94a3b8; font-style: italic; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:99px;
            font-size:12px; font-weight:600; }}
  .body-text {{ color:#475569; margin: 8px 0 0; white-space: pre-wrap; }}
  .meta {{ color:#64748b; font-size:13px; margin-top:4px; }}
  code {{ font-family: ui-monospace, monospace; background:#f1f5f9;
          padding:1px 5px; border-radius:3px; font-size:12px; }}
  .header-row {{ display:flex; justify-content:space-between; align-items:flex-start; }}
</style>
</head>
<body>

<div class="card">
  <div class="header-row">
    <div>
      <h1>Deploy Report</h1>
      <p class="meta">Generated {now} &nbsp;·&nbsp; Commit <code>{short}</code> &nbsp;·&nbsp; {date}</p>
    </div>
    <div>
      {'<span class="badge" style="background:#fef2f2;color:#dc2626;border:1px solid #fca5a5">⚠ REGRESSIONS</span>' if has_regressions else '<span class="badge" style="background:#f0fdf4;color:#16a34a;border:1px solid #86efac">✓ CLEAN</span>'}
    </div>
  </div>
  <hr style="margin:12px 0;border:none;border-top:1px solid #e2e8f0">
  <h2>Commit</h2>
  <p style="margin:0;font-size:15px;font-weight:600">{subject}</p>
  {body_section}
</div>

{regression_banner}

<div class="card">
  <h2>Changed Files — {diff_summary}</h2>
  <table>
    <tr><th>File</th></tr>
    {files_html}
  </table>
</div>

<div class="card">
  <h2>Benchmarks &nbsp;<span style="font-size:12px;font-weight:400;color:#64748b">{perf_header}</span></h2>
  <table>
    <tr>
      <th>Test</th>
      <th style="text-align:right">Prev (ms)</th>
      <th style="text-align:right">Current (ms)</th>
      <th style="text-align:right">Delta</th>
      <th style="text-align:center">Status</th>
    </tr>
    {bench_html}
  </table>
  <p style="margin:10px 0 0;font-size:12px;color:#94a3b8">
    ⬆ improved &gt;20% &nbsp;|&nbsp; ⚠ slow &gt;20% &nbsp;|&nbsp; ✗ regression &gt;50% &nbsp;|&nbsp; + new test
  </p>
</div>

<div class="card">
  <h2>Recent Commits</h2>
  <table>
    <tr><th>Hash</th><th>Subject</th></tr>
    {log_html}
  </table>
</div>

</body>
</html>
"""
    return html


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    os.makedirs(CHECKINS_DIR, exist_ok=True)

    commit_info = get_commit_info()
    changed_files, diff_summary = get_changed_files()
    recent_log = get_commit_log(10)
    rows = load_perf_csv()
    bench_rows, latest_commit, prev_commit = benchmark_comparison(rows)

    html = generate_html(
        commit_info, changed_files, diff_summary, recent_log,
        bench_rows, latest_commit, prev_commit,
    )

    date_tag = datetime.now().strftime('%Y-%m-%d')
    short    = commit_info['short_hash']
    filename = f'{date_tag}_{short}.html'
    out_path = os.path.join(CHECKINS_DIR, filename)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'Report written -> checkins/{filename}')

    # Print a quick console summary
    regressions = [r for r in bench_rows if r['flag'] == 'regression']
    if regressions:
        print(f'\n[!] {len(regressions)} benchmark regression(s):')
        for r in regressions:
            print(f'   {r["test"]}: {_fmt_ms(r["prev_ms"])} -> {_fmt_ms(r["current_ms"])} ms  ({_fmt_delta(r["delta_pct"])})')
    else:
        print('[ok] No benchmark regressions.')


if __name__ == '__main__':
    main()
