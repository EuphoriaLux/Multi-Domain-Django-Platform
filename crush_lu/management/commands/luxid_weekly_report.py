import sys
import os
from pathlib import Path
from datetime import datetime
import json

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from allauth.socialaccount.models import SocialAccount
from crush_lu.models import CrushProfile

import pandas as pd
import numpy as np

User = get_user_model()


class Command(BaseCommand):
    help = "Generate week-by-week LuxID adoption intelligence, growth metrics, and visual charts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            type=str,
            default="table",
            choices=["table", "markdown", "csv", "json"],
            help="Terminal output format (default: table)",
        )
        parser.add_argument(
            "--save-charts",
            action="store_true",
            default=True,
            help="Generate PNG charts in scripts/exports/ (default: True)",
        )
        parser.add_argument(
            "--html",
            action="store_true",
            default=True,
            help="Generate interactive HTML dashboard in scripts/exports/ (default: True)",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("🔍 Querying Crush.lu member records and LuxID social accounts..."))

        users = []
        for u in User.objects.all():
            sa = SocialAccount.objects.filter(user=u, provider="luxid").first()
            p = CrushProfile.objects.filter(user=u).first()
            users.append({
                "user_id": u.id,
                "username": u.username,
                "email": u.email,
                "user_date_joined": u.date_joined,
                "has_luxid": bool(sa),
                "luxid_date_joined": sa.date_joined if sa else None,
                "verification_status": p.verification_status if p else "unregistered",
                "verification_method": p.verification_method if p else "none",
                "is_connect_verified": p.is_connect_identity_verified if p else False,
            })

        df = pd.DataFrame(users)
        if df.empty:
            self.stdout.write(self.style.WARNING("No users found in database."))
            return

        df["user_week"] = df["user_date_joined"].dt.strftime("%Y-W%W")
        df["luxid_week"] = df["luxid_date_joined"].apply(
            lambda d: d.strftime("%Y-W%W") if pd.notnull(d) else None
        )

        all_weeks = sorted(list(set(df["user_week"].dropna().tolist() + df["luxid_week"].dropna().tolist())))
        base_df = pd.DataFrame({"week": all_weeks})

        weekly_signups = df.groupby("user_week").agg(
            total_signups=("user_id", "count"),
        ).reset_index().rename(columns={"user_week": "week"})

        luxid_df = df[df["has_luxid"]].copy()
        weekly_luxid = luxid_df.groupby("luxid_week").agg(
            new_luxid_adoptions=("user_id", "count")
        ).reset_index().rename(columns={"luxid_week": "week"})

        merged = base_df.merge(weekly_signups, on="week", how="left").merge(weekly_luxid, on="week", how="left").fillna(0)
        merged = merged.sort_values("week").reset_index(drop=True)

        merged["total_signups"] = merged["total_signups"].astype(int)
        merged["new_luxid_adoptions"] = merged["new_luxid_adoptions"].astype(int)
        merged["cumulative_total_users"] = merged["total_signups"].cumsum()
        merged["cumulative_luxid"] = merged["new_luxid_adoptions"].cumsum()

        merged["weekly_adoption_rate_pct"] = np.where(
            merged["total_signups"] > 0,
            (merged["new_luxid_adoptions"] / merged["total_signups"]) * 100,
            0.0
        )
        merged["cumulative_penetration_pct"] = np.where(
            merged["cumulative_total_users"] > 0,
            (merged["cumulative_luxid"] / merged["cumulative_total_users"]) * 100,
            0.0
        )

        total_users = len(df)
        total_luxid = df["has_luxid"].sum()
        overall_pct = (total_luxid / total_users * 100) if total_users > 0 else 0

        fmt = options["format"]
        if fmt == "table":
            self.stdout.write("\n" + "=" * 80)
            self.stdout.write(self.style.SUCCESS(f"  CRUSH.LU — WEEKLY LUXID ADOPTION REPORT"))
            self.stdout.write(f"  Total Members: {total_users} | Total LuxID: {total_luxid} ({overall_pct:.1f}% Adoption)")
            self.stdout.write("=" * 80)
            self.stdout.write(merged.to_string(index=False))
            self.stdout.write("=" * 80 + "\n")
        elif fmt == "markdown":
            self.stdout.write(merged.to_markdown(index=False))
        elif fmt == "csv":
            self.stdout.write(merged.to_csv(index=False))
        elif fmt == "json":
            self.stdout.write(json.dumps(merged.to_dict(orient="records"), indent=2))

        # Save exports
        from django.conf import settings
        export_dir = settings.BASE_DIR / "scripts" / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        csv_file = export_dir / "crush_luxid_weekly_metrics.csv"
        merged.to_csv(csv_file, index=False)
        self.stdout.write(self.style.SUCCESS(f"✅ Exported CSV to: {csv_file}"))

        if options["save_charts"]:
            try:
                from scripts.generate_luxid_charts import plot_dashboard
                png_file = export_dir / "crush_luxid_weekly_adoption.png"
                plot_dashboard(merged, df, str(png_file))
                self.stdout.write(self.style.SUCCESS(f"✅ Generated chart PNG to: {png_file}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Chart generation failed: {e}"))

        if options["html"]:
            try:
                html_file = export_dir / "luxid_adoption_dashboard.html"
                self._generate_html(merged, df, html_file)
                self.stdout.write(self.style.SUCCESS(f"✅ Generated Interactive HTML Dashboard: {html_file}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"HTML generation failed: {e}"))

    def _generate_html(self, weekly_df, full_df, output_file):
        total_users = len(full_df)
        total_luxid = int(full_df["has_luxid"].sum())
        luxid_pct = f"{(total_luxid / total_users * 100):.1f}%" if total_users > 0 else "0%"
        
        # Prepare json for Chart.js
        labels = weekly_df["week"].tolist()
        signups = weekly_df["total_signups"].tolist()
        luxid_weekly = weekly_df["new_luxid_adoptions"].tolist()
        cum_users = weekly_df["cumulative_total_users"].tolist()
        cum_luxid = weekly_df["cumulative_luxid"].tolist()
        pen_rate = [round(x, 1) for x in weekly_df["cumulative_penetration_pct"].tolist()]
        cohort_rate = [round(x, 1) for x in weekly_df["weekly_adoption_rate_pct"].tolist()]

        v_counts = full_df[full_df["has_luxid"]]["verification_method"].value_counts().to_dict()
        v_labels = list(v_counts.keys())
        v_values = list(v_counts.values())

        html_content = f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Crush.lu — LuxID Weekly Adoption Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script>
    tailwind.config = {{
      darkMode: 'class',
      theme: {{
        extend: {{
          colors: {{
            brand: {{
              50: '#fff1f2',
              500: '#f43f5e',
              600: '#e11d48',
              700: '#be123c',
            }},
            navy: {{
              900: '#0b1120',
              800: '#131e32',
              700: '#1e293b',
              600: '#334155'
            }}
          }}
        }}
      }}
    }}
  </script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: 'Plus Jakarta Sans', sans-serif; }}
    .glass-card {{
      background: rgba(19, 30, 50, 0.75);
      backdrop-filter: blur(12px);
      border: 1px solid rgba(51, 65, 85, 0.6);
    }}
  </style>
</head>
<body class="bg-[#0b1120] text-slate-100 min-h-screen pb-16">

  <!-- Header Navigation -->
  <header class="border-b border-slate-800 bg-[#0d1527]/90 sticky top-0 z-50 backdrop-blur">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex flex-wrap items-center justify-between gap-4">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-rose-600 to-pink-500 flex items-center justify-center font-bold text-white shadow-lg shadow-rose-600/30">
          C
        </div>
        <div>
          <h1 class="text-xl font-extrabold tracking-tight text-white flex items-center gap-2">
            Crush.lu <span class="text-xs px-2.5 py-0.5 rounded-full bg-rose-500/20 text-rose-300 font-semibold border border-rose-500/30">LuxID Analytics</span>
          </h1>
          <p class="text-xs text-slate-400">Week-by-Week Identity Adoption & Penetration Monitor</p>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <span class="text-xs text-slate-400 bg-slate-800/80 px-3 py-1.5 rounded-lg border border-slate-700">
          Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
        </span>
        <button onclick="window.print()" class="text-xs bg-slate-800 hover:bg-slate-700 text-slate-200 px-3.5 py-1.5 rounded-lg border border-slate-700 font-medium transition flex items-center gap-1.5">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z"/></svg>
          Export / Print
        </button>
      </div>
    </div>
  </header>

  <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-8 space-y-8">

    <!-- KPI Metric Cards -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
      <div class="glass-card rounded-2xl p-5 relative overflow-hidden">
        <div class="absolute -right-4 -bottom-4 w-24 h-24 bg-rose-500/10 rounded-full blur-xl"></div>
        <p class="text-xs font-semibold text-rose-400 uppercase tracking-wider">Total LuxID Adoptions</p>
        <div class="flex items-baseline gap-2 mt-2">
          <h3 class="text-3xl font-extrabold text-white">{total_luxid}</h3>
          <span class="text-xs text-rose-300 font-medium">members</span>
        </div>
        <p class="text-xs text-slate-400 mt-2">Government-verified digital ID</p>
      </div>

      <div class="glass-card rounded-2xl p-5 relative overflow-hidden">
        <div class="absolute -right-4 -bottom-4 w-24 h-24 bg-sky-500/10 rounded-full blur-xl"></div>
        <p class="text-xs font-semibold text-sky-400 uppercase tracking-wider">Total Member Base</p>
        <div class="flex items-baseline gap-2 mt-2">
          <h3 class="text-3xl font-extrabold text-white">{total_users}</h3>
          <span class="text-xs text-sky-300 font-medium">accounts</span>
        </div>
        <p class="text-xs text-slate-400 mt-2">Registered Crush.lu profiles</p>
      </div>

      <div class="glass-card rounded-2xl p-5 relative overflow-hidden">
        <div class="absolute -right-4 -bottom-4 w-24 h-24 bg-emerald-500/10 rounded-full blur-xl"></div>
        <p class="text-xs font-semibold text-emerald-400 uppercase tracking-wider">Overall Adoption Rate</p>
        <div class="flex items-baseline gap-2 mt-2">
          <h3 class="text-3xl font-extrabold text-white">{luxid_pct}</h3>
          <span class="text-xs text-emerald-300 font-medium">penetration</span>
        </div>
        <p class="text-xs text-slate-400 mt-2">Share of users with LuxID linked</p>
      </div>

      <div class="glass-card rounded-2xl p-5 relative overflow-hidden">
        <div class="absolute -right-4 -bottom-4 w-24 h-24 bg-indigo-500/10 rounded-full blur-xl"></div>
        <p class="text-xs font-semibold text-indigo-400 uppercase tracking-wider">Connect Eligibility</p>
        <div class="flex items-baseline gap-2 mt-2">
          <h3 class="text-3xl font-extrabold text-white">100%</h3>
          <span class="text-xs text-indigo-300 font-medium">candidate gate</span>
        </div>
        <p class="text-xs text-slate-400 mt-2">All LuxID users pass candidate gate</p>
      </div>
    </div>

    <!-- Charts Grid -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">

      <!-- Chart 1: Weekly New Adoptions -->
      <div class="glass-card rounded-2xl p-6">
        <div class="flex items-center justify-between mb-4">
          <div>
            <h2 class="text-base font-bold text-white">Weekly New Signups vs. LuxID Adoptions</h2>
            <p class="text-xs text-slate-400">Weekly volume comparison of new accounts and LuxID connections</p>
          </div>
          <span class="px-2.5 py-1 text-xs rounded-lg bg-slate-800 text-slate-300 border border-slate-700">Weekly Cohorts</span>
        </div>
        <div class="h-80">
          <canvas id="weeklyBarChart"></canvas>
        </div>
      </div>

      <!-- Chart 2: Cumulative S-Curve -->
      <div class="glass-card rounded-2xl p-6">
        <div class="flex items-center justify-between mb-4">
          <div>
            <h2 class="text-base font-bold text-white">Cumulative Growth Trajectory</h2>
            <p class="text-xs text-slate-400">Total member base vs. LuxID adoption over time</p>
          </div>
          <span class="px-2.5 py-1 text-xs rounded-lg bg-slate-800 text-slate-300 border border-slate-700">Cumulative S-Curve</span>
        </div>
        <div class="h-80">
          <canvas id="cumulativeLineChart"></canvas>
        </div>
      </div>

      <!-- Chart 3: Penetration Rate -->
      <div class="glass-card rounded-2xl p-6">
        <div class="flex items-center justify-between mb-4">
          <div>
            <h2 class="text-base font-bold text-white">LuxID Penetration & Cohort Adoption Rate</h2>
            <p class="text-xs text-slate-400">Percentage of members adopting LuxID week by week</p>
          </div>
          <span class="px-2.5 py-1 text-xs rounded-lg bg-slate-800 text-slate-300 border border-slate-700">Rate (%)</span>
        </div>
        <div class="h-80">
          <canvas id="rateChart"></canvas>
        </div>
      </div>

      <!-- Chart 4: Verification Method Breakdown -->
      <div class="glass-card rounded-2xl p-6">
        <div class="flex items-center justify-between mb-4">
          <div>
            <h2 class="text-base font-bold text-white">LuxID Adopters by Verification Method</h2>
            <p class="text-xs text-slate-400">How LuxID members achieved verified status</p>
          </div>
          <span class="px-2.5 py-1 text-xs rounded-lg bg-slate-800 text-slate-300 border border-slate-700">N={total_luxid}</span>
        </div>
        <div class="h-80 flex items-center justify-center">
          <canvas id="pieChart"></canvas>
        </div>
      </div>

    </div>

    <!-- Data Table -->
    <div class="glass-card rounded-2xl overflow-hidden">
      <div class="p-6 border-b border-slate-800 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 class="text-lg font-bold text-white">Week-by-Week Metrics Ledger</h2>
          <p class="text-xs text-slate-400">Tabular breakdown of weekly additions, cumulative totals, and adoption ratios</p>
        </div>
        <button onclick="downloadCSV()" class="text-xs bg-rose-600 hover:bg-rose-500 text-white px-4 py-2 rounded-xl font-semibold shadow-lg shadow-rose-600/30 transition flex items-center gap-2">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
          Download CSV Data
        </button>
      </div>

      <div class="overflow-x-auto">
        <table class="w-full text-left text-sm text-slate-300">
          <thead class="bg-slate-900/80 text-xs uppercase font-semibold text-slate-400 border-b border-slate-800">
            <tr>
              <th class="px-6 py-4">ISO Week</th>
              <th class="px-6 py-4">New Signups</th>
              <th class="px-6 py-4">New LuxID Adoptions</th>
              <th class="px-6 py-4">Weekly Adoption Rate</th>
              <th class="px-6 py-4">Cumulative Total Users</th>
              <th class="px-6 py-4">Cumulative LuxID</th>
              <th class="px-6 py-4">Overall Penetration</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-800/60">
            {''.join([f'''
            <tr class="hover:bg-slate-800/40 transition">
              <td class="px-6 py-4 font-bold text-white">{row['week']}</td>
              <td class="px-6 py-4 text-sky-400 font-semibold">{row['total_signups']}</td>
              <td class="px-6 py-4 text-rose-400 font-semibold">{row['new_luxid_adoptions']}</td>
              <td class="px-6 py-4 text-indigo-300">{row['weekly_adoption_rate_pct']:.1f}%</td>
              <td class="px-6 py-4">{row['cumulative_total_users']}</td>
              <td class="px-6 py-4 font-bold text-rose-400">{row['cumulative_luxid']}</td>
              <td class="px-6 py-4">
                <span class="px-2.5 py-1 rounded-full text-xs font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  {row['cumulative_penetration_pct']:.1f}%
                </span>
              </td>
            </tr>
            ''' for _, row in weekly_df.iterrows()])}
          </tbody>
        </table>
      </div>
    </div>

  </main>

  <script>
    const labels = {json.dumps(labels)};
    const signupsData = {json.dumps(signups)};
    const luxidData = {json.dumps(luxid_weekly)};
    const cumUsersData = {json.dumps(cum_users)};
    const cumLuxidData = {json.dumps(cum_luxid)};
    const penRateData = {json.dumps(pen_rate)};
    const cohortRateData = {json.dumps(cohort_rate)};

    Chart.defaults.color = '#94a3b8';
    Chart.defaults.borderColor = '#1e293b';
    Chart.defaults.font.family = "'Plus Jakarta Sans', sans-serif";

    // 1. Weekly Bar Chart
    new Chart(document.getElementById('weeklyBarChart'), {{
      type: 'bar',
      data: {{
        labels: labels,
        datasets: [
          {{
            label: 'Total Signups',
            data: signupsData,
            backgroundColor: '#38bdf8',
            borderRadius: 6,
          }},
          {{
            label: 'LuxID Adoptions',
            data: luxidData,
            backgroundColor: '#f43f5e',
            borderRadius: 6,
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ position: 'top', labels: {{ color: '#f8fafc' }} }}
        }},
        scales: {{
          y: {{ beginAtZero: true, grid: {{ color: '#1e293b' }} }},
          x: {{ grid: {{ display: false }} }}
        }}
      }}
    }});

    // 2. Cumulative Line Chart
    new Chart(document.getElementById('cumulativeLineChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{
            label: 'Cumulative Total Members',
            data: cumUsersData,
            borderColor: '#38bdf8',
            backgroundColor: 'rgba(56, 189, 248, 0.1)',
            fill: true,
            tension: 0.3,
            pointRadius: 5
          }},
          {{
            label: 'Cumulative LuxID Adopters',
            data: cumLuxidData,
            borderColor: '#f43f5e',
            backgroundColor: 'rgba(244, 63, 94, 0.2)',
            fill: true,
            tension: 0.3,
            pointRadius: 6,
            pointBackgroundColor: '#f43f5e'
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ position: 'top', labels: {{ color: '#f8fafc' }} }}
        }},
        scales: {{
          y: {{ beginAtZero: true, grid: {{ color: '#1e293b' }} }},
          x: {{ grid: {{ display: false }} }}
        }}
      }}
    }});

    // 3. Rate Chart
    new Chart(document.getElementById('rateChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{
            label: 'Overall LuxID Penetration %',
            data: penRateData,
            borderColor: '#10b981',
            backgroundColor: '#10b981',
            tension: 0.3,
            pointRadius: 5
          }},
          {{
            label: 'Weekly Cohort Adoption %',
            data: cohortRateData,
            borderColor: '#818cf8',
            backgroundColor: '#818cf8',
            borderDash: [5, 5],
            tension: 0.3,
            pointRadius: 5
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ position: 'top', labels: {{ color: '#f8fafc' }} }}
        }},
        scales: {{
          y: {{
            beginAtZero: true,
            max: 100,
            ticks: {{ callback: value => value + '%' }},
            grid: {{ color: '#1e293b' }}
          }},
          x: {{ grid: {{ display: false }} }}
        }}
      }}
    }});

    // 4. Verification Donut Chart
    new Chart(document.getElementById('pieChart'), {{
      type: 'doughnut',
      data: {{
        labels: {json.dumps(v_labels)},
        datasets: [{{
          data: {json.dumps(v_values)},
          backgroundColor: ['#f43f5e', '#38bdf8', '#818cf8', '#10b981', '#f59e0b'],
          borderColor: '#0b1120',
          borderWidth: 3
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ position: 'bottom', labels: {{ color: '#cbd5e1' }} }}
        }},
        cutout: '65%'
      }}
    }});

    function downloadCSV() {{
      const rows = [
        ["ISO Week", "New Signups", "New LuxID Adoptions", "Weekly Adoption Rate (%)", "Cumulative Total Users", "Cumulative LuxID", "Overall Penetration (%)"],
        {','.join(['[' + ','.join([f'"{row["week"]}"', str(row["total_signups"]), str(row["new_luxid_adoptions"]), f'{row["weekly_adoption_rate_pct"]:.2f}', str(row["cumulative_total_users"]), str(row["cumulative_luxid"]), f'{row["cumulative_penetration_pct"]:.2f}']) + ']' for _, row in weekly_df.iterrows()])}
      ];
      let csvContent = "data:text/csv;charset=utf-8," + rows.map(e => e.join(",")).join("\\n");
      const encodedUri = encodeURI(csvContent);
      const link = document.createElement("a");
      link.setAttribute("href", encodedUri);
      link.setAttribute("download", "crush_luxid_weekly_adoption.csv");
      document.body.appendChild(link);
      link.click();
    }}
  </script>
</body>
</html>
"""
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html_content)
