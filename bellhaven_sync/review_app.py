"""Accessible, low-glare local web UI for reviewing, approving, and rejecting CRM proposals."""

from __future__ import annotations

import html
import json
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .models import Proposal
from .service import SyncService


def _esc(val: Any) -> str:
    return html.escape(str(val if val is not None else ""))


def _render_evidence_html(prop: Proposal) -> str:
    ev = prop.evidence
    blocks: list[str] = []

    if "field_diffs" in ev:
        rows = "".join(
            f"<tr><td><code>{_esc(r['field'])}</code></td>"
            f"<td class='old-val'>{_esc(r['crm_current'])}</td>"
            f"<td class='new-val'>{_esc(r['website_target'])}</td></tr>"
            for r in ev["field_diffs"]
        )
        blocks.append(
            f"<div class='ev-box'><h4>Field Differences</h4>"
            f"<table><thead><tr><th>Field</th><th>CRM Current</th><th>Website Target</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
        )

    if "crm_old_account" in ev:
        old = ev["crm_old_account"]
        blocks.append(
            f"<div class='ev-box warn-box'><h4>CHOW Billing Guard</h4>"
            f"<p><strong>Old Account:</strong> {_esc(old['name'])} (<code>{_esc(old['account_id'])}</code>)</p>"
            f"<p><strong>Parent:</strong> {_esc(old['parent_name'])} | "
            f"<strong>Revenue:</strong> ${old['lifetime_revenue']:,.0f} | "
            f"<strong>Open AR:</strong> ${old['outstanding_ar']:,.0f}</p>"
            f"<p><strong>Rule:</strong> {_esc(ev.get('sop_rule', ''))}</p></div>"
        )

    if "candidate_crm_account" in ev:
        c = ev["candidate_crm_account"]
        site = ev.get("website", {})
        blocks.append(
            f"<div class='ev-box danger-box'><h4>Candidate vs Website Comparison</h4>"
            f"<p><strong>Website:</strong> {_esc(site.get('name'))} | {_esc(site.get('street'))}, "
            f"{_esc(site.get('city'))}, {_esc(site.get('state'))} {_esc(site.get('zip_code'))} | "
            f"Admin: {_esc(site.get('administrator'))}</p>"
            f"<p><strong>CRM Candidate:</strong> {_esc(c['name'])} (<code>{_esc(c['account_id'])}</code>) | "
            f"{_esc(c['billing_address'])} | Parent: {_esc(c['parent'])}</p>"
            f"<p><strong>Recommendation:</strong> {_esc(ev.get('recommendation', ''))}</p></div>"
        )

    if "losing_account" in ev and "surviving_account" in ev:
        loser = ev["losing_account"]
        winner = ev["surviving_account"]
        blocks.append(
            f"<div class='ev-box'><h4>Duplicate Cluster</h4>"
            f"<p><strong>Keep Survivor:</strong> {_esc(winner['name'])} (<code>{_esc(winner['account_id'])}</code>) [{_esc(winner['parent'])}]</p>"
            f"<p><strong>Inactivate Duplicate:</strong> {_esc(loser['name'])} (<code>{_esc(loser['account_id'])}</code>) [{_esc(loser['parent'])}] "
            f"(Revenue ${loser['revenue']:,.0f}, AR ${loser['ar']:,.0f})</p></div>"
        )

    if "old_bellhaven_account" in ev and "successor_crm_account" in ev:
        old = ev["old_bellhaven_account"]
        succ = ev["successor_crm_account"]
        blocks.append(
            f"<div class='ev-box warn-box'><h4>Offboarded CHOW Evidence</h4>"
            f"<p><strong>Existing Bellhaven Account:</strong> {_esc(old['name'])} (<code>{_esc(old['account_id'])}</code>) | "
            f"Rev ${old['revenue']:,.0f}, AR ${old['ar']:,.0f}</p>"
            f"<p><strong>CRM Successor at Same Street:</strong> {_esc(succ['name'])} (<code>{_esc(succ['account_id'])}</code>) | "
            f"Parent: {_esc(succ['parent'])}</p></div>"
        )

    if "website" in ev and "candidate_crm_account" not in ev:
        w = ev["website"]
        blocks.append(
            f"<div class='ev-box'><h4>Website Source Record</h4>"
            f"<p><a href='{_esc(w.get('url', '#'))}' target='_blank' rel='noopener'>{_esc(w.get('url', ''))}</a> "
            f"(discovered via <code>{_esc(w.get('discovered_from', 'directory'))}</code>)</p>"
            f"<p>{_esc(w.get('street'))}, {_esc(w.get('city'))}, {_esc(w.get('state'))} {_esc(w.get('zip_code'))} | "
            f"Phone: {_esc(w.get('phone'))} | Admin: {_esc(w.get('administrator'))} | "
            f"Care: {_esc(', '.join(w.get('care_offerings') or []))}</p></div>"
        )

    if "crm_account" in ev:
        c = ev["crm_account"]
        blocks.append(
            f"<div class='ev-box'><h4>CRM Account Snapshot</h4>"
            f"<p><code>{_esc(c['account_id'])}</code> | {_esc(c['address'])} | Status: {_esc(c['status'])} | "
            f"Rev ${c['revenue']:,.0f}, AR ${c['ar']:,.0f}</p></div>"
        )

    return "".join(blocks)


def render_page(service: SyncService, active_tab: str = "pending", banner: str = "") -> str:
    counts = service.store.counts()
    latest = service.store.latest_sync_run()
    proposals = service.store.list_proposals(None if active_tab == "all" else active_tab)

    sync_meta = "No sync run recorded yet."
    if latest:
        sync_meta = (
            f"Last sync: {latest['started_at']} | Website communities: {latest['website_count']} | "
            f"CRM accounts: {latest['crm_account_count']} | Contacts: {latest['crm_contact_count']} | "
            f"Skipped already-decided: {latest['skipped_decided_count']}"
        )

    cards_html: list[str] = []
    for p in proposals:
        rec_badge = (
            "<span class='pill pill-reject'>Recommend Reject</span>"
            if p.recommended_decision == "reject"
            else "<span class='pill pill-approve'>Recommend Approve</span>"
        )
        status_badge = f"<span class='pill pill-{_esc(p.decision_status)}'>{_esc(p.decision_status.upper())}</span>"
        ops_pretty = json.dumps(p.operations, indent=2)
        actions_html = ""
        if p.decision_status == "pending":
            actions_html = f"""
            <div class='card-actions'>
              <form method='post' action='/decision'>
                <input type='hidden' name='fingerprint' value='{_esc(p.fingerprint)}'>
                <input type='hidden' name='tab' value='{_esc(active_tab)}'>
                <input type='text' name='note' placeholder='Optional reviewer note...' class='note-input'>
                <button type='submit' name='decision' value='approve' class='btn btn-approve'>Approve &amp; Apply to CRM</button>
                <button type='submit' name='decision' value='reject' class='btn btn-reject'>Reject</button>
              </form>
            </div>
            """
        else:
            exec_json = json.dumps(p.execution_result, indent=2) if p.execution_result else ""
            actions_html = (
                f"<div class='decided-note'><strong>Note:</strong> {_esc(p.decision_note)}"
                + (f"<pre>{_esc(exec_json)}</pre>" if exec_json and p.decision_status == "approved" else "")
                + "</div>"
            )

        cards_html.append(
            f"""
            <article class='card'>
              <div class='card-head'>
                <div>
                  <span class='pill pill-cat'>{_esc(p.category)}</span>
                  {rec_badge}
                  {status_badge}
                  <h3>{_esc(p.title)}</h3>
                </div>
                <div class='acct-id'>Account: <code>{_esc(p.target_account_id)}</code></div>
              </div>
              <p class='summary'>{_esc(p.summary)}</p>
              {_render_evidence_html(p)}
              <details>
                <summary>Planned CRM API Operations ({len(p.operations)} step(s))</summary>
                <pre>{_esc(ops_pretty)}</pre>
              </details>
              {actions_html}
            </article>
            """
        )

    empty_state = "<div class='card'><p>No proposals in this view.</p></div>"
    banner_html = f"<div class='banner'>{_esc(banner)}</div>" if banner else ""

    tabs = [
        ("pending", f"Pending ({counts['pending']})"),
        ("approved", f"Approved ({counts['approved']})"),
        ("rejected", f"Rejected ({counts['rejected']})"),
        ("all", f"All ({sum(counts.values())})"),
    ]
    tabs_html = "".join(
        f"<a class='tab {'active' if active_tab == key else ''}' href='/?tab={key}'>{_esc(label)}</a>"
        for key, label in tabs
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bellhaven CRM Sync Review Queue</title>
  <style>
    :root {{
      --bg: #F6F4EC;
      --surface: #FFFFFF;
      --ink: #1F2421;
      --muted: #4E5851;
      --border: #D9D4C3;
      --accent: #245749;
      --approve: #1F6E43;
      --reject: #9C2F2F;
      --warn-bg: #FCF6E5;
      --warn-border: #C58B16;
      --danger-bg: #FDF0F0;
      --danger-border: #B84242;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      font-size: 16px;
      line-height: 1.55;
      color: var(--ink);
      background: var(--bg);
    }}
    header {{
      background: var(--accent);
      color: #FFFFFF;
      padding: 24px 32px;
    }}
    .wrap {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 28px 24px 64px;
    }}
    header .inner {{
      max-width: 1120px;
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
    }}
    h1 {{ margin: 0 0 6px; font-size: 26px; font-weight: 700; }}
    .meta {{ font-size: 14px; color: #E2ECE8; }}
    .toolbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 14px;
      margin-bottom: 22px;
    }}
    .tabs {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .tab {{
      text-decoration: none;
      color: var(--ink);
      background: var(--surface);
      border: 1px solid var(--border);
      padding: 10px 18px;
      border-radius: 8px;
      font-weight: 600;
    }}
    .tab.active {{
      background: var(--accent);
      color: #FFFFFF;
      border-color: var(--accent);
    }}
    .btn {{
      cursor: pointer;
      border: none;
      border-radius: 8px;
      padding: 11px 18px;
      font-size: 15px;
      font-weight: 600;
      text-decoration: none;
      display: inline-block;
    }}
    .btn-sync {{ background: #FFFFFF; color: var(--accent); }}
    .btn-crm {{ background: #E9E4D4; color: var(--ink); }}
    .btn-approve {{ background: var(--approve); color: #FFFFFF; }}
    .btn-reject {{ background: var(--reject); color: #FFFFFF; }}
    .banner {{
      background: #E8F3EC;
      border: 1px solid #88BA9D;
      border-radius: 8px;
      padding: 14px 18px;
      margin-bottom: 20px;
      font-weight: 600;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 22px 24px;
      margin-bottom: 20px;
    }}
    .card-head {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      flex-wrap: wrap;
    }}
    .card h3 {{ margin: 10px 0 8px; font-size: 20px; }}
    .acct-id {{ font-size: 14px; color: var(--muted); }}
    .summary {{ margin: 0 0 16px; color: var(--ink); }}
    .pill {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12.5px;
      font-weight: 700;
      margin-right: 8px;
    }}
    .pill-cat {{ background: #E7EBE9; color: #203A31; }}
    .pill-approve, .pill-approved {{ background: #DFF2E6; color: #165934; }}
    .pill-reject, .pill-rejected {{ background: #FBE6E6; color: #8A2424; }}
    .pill-pending {{ background: #F4ECD4; color: #6E5109; }}
    .ev-box {{
      background: #FAF9F4;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 16px;
      margin-bottom: 14px;
    }}
    .ev-box h4 {{ margin: 0 0 8px; font-size: 15px; color: var(--accent); }}
    .ev-box p {{ margin: 4px 0; }}
    .warn-box {{ background: var(--warn-bg); border-color: var(--warn-border); }}
    .danger-box {{ background: var(--danger-bg); border-color: var(--danger-border); }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
    th, td {{
      text-align: left;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      font-size: 15px;
    }}
    .old-val {{ color: #8A2424; }}
    .new-val {{ color: #165934; font-weight: 600; }}
    details {{ margin-top: 10px; }}
    summary {{ cursor: pointer; font-weight: 600; color: var(--muted); }}
    pre {{
      background: #F3F1E8;
      padding: 12px;
      border-radius: 8px;
      overflow-x: auto;
      font-size: 13.5px;
    }}
    .card-actions form {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
      margin-top: 16px;
      padding-top: 16px;
      border-top: 1px solid var(--border);
    }}
    .note-input {{
      flex: 1 1 260px;
      padding: 10px 12px;
      font-size: 15px;
      border: 1px solid var(--border);
      border-radius: 8px;
    }}
    .decided-note {{
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid var(--border);
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <header>
    <div class="inner">
      <div>
        <h1>Bellhaven Senior Living CRM Sync</h1>
        <div class="meta">{_esc(sync_meta)}</div>
      </div>
      <div style="display:flex; gap:10px; flex-wrap:wrap;">
        <a class="btn btn-crm" href="{_esc(service.settings.crm_browser_url)}" target="_blank" rel="noopener">Open Read-Only CRM Browser</a>
        <form method="post" action="/sync" style="margin:0;">
          <input type="hidden" name="tab" value="{_esc(active_tab)}">
          <button type="submit" class="btn btn-sync">Run Daily Sync Now</button>
        </form>
      </div>
    </div>
  </header>
  <main class="wrap">
    {banner_html}
    <div class="toolbar">
      <div class="tabs">{tabs_html}</div>
    </div>
    {"".join(cards_html) if cards_html else empty_state}
  </main>
</body>
</html>"""


def make_handler(service: SyncService) -> type[BaseHTTPRequestHandler]:
    class ReviewHandler(BaseHTTPRequestHandler):
        def _send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _redirect(self, tab: str, message: str = "") -> None:
            query = urllib.parse.urlencode({"tab": tab, **({"msg": message} if message else {})})
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", f"/?{query}")
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/":
                self._send_html("<h1>Not Found</h1>", HTTPStatus.NOT_FOUND)
                return
            qs = urllib.parse.parse_qs(parsed.query)
            tab = qs.get("tab", ["pending"])[0]
            if tab not in ("pending", "approved", "rejected", "all"):
                tab = "pending"
            banner = qs.get("msg", [""])[0]
            self._send_html(render_page(service, active_tab=tab, banner=banner))

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            form = urllib.parse.parse_qs(raw)
            tab = form.get("tab", ["pending"])[0]

            if self.path == "/sync":
                stats = service.run_sync()
                msg = (
                    f"Sync complete: {stats['website_communities']} site communities, "
                    f"{stats['new_pending']} new pending, {stats['skipped_decided']} already decided skipped."
                )
                self._redirect("pending", msg)
                return

            if self.path == "/decision":
                fingerprint = form.get("fingerprint", [""])[0]
                decision = form.get("decision", [""])[0]
                note = form.get("note", [""])[0].strip()
                try:
                    if decision == "approve":
                        service.approve(fingerprint, note)
                        self._redirect(tab, f"Approved and applied proposal {fingerprint[:8]} to live CRM.")
                    elif decision == "reject":
                        service.reject(fingerprint, note)
                        self._redirect(tab, f"Rejected proposal {fingerprint[:8]}; suppressed from future syncs.")
                    else:
                        self._redirect(tab, "Unknown decision action.")
                except Exception as exc:
                    self._redirect(tab, f"Error applying decision: {exc}")
                return

            self._send_html("<h1>Not Found</h1>", HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ReviewHandler


def _bind_review_server(service: SyncService, host: str, preferred_port: int) -> tuple[ThreadingHTTPServer, int]:
    """Bind the review server on preferred_port, stepping up if Windows/local services hold the port."""
    handler = make_handler(service)
    last_error: OSError | None = None
    for port in range(preferred_port, preferred_port + 21):
        try:
            return ThreadingHTTPServer((host, port), handler), port
        except OSError as exc:
            last_error = exc
    raise last_error  # type: ignore[misc]


def serve_review_app(service: SyncService) -> None:
    host = service.settings.review_host
    preferred_port = service.settings.review_port
    server, port = _bind_review_server(service, host, preferred_port)
    if port != preferred_port:
        print(f"Port {preferred_port} unavailable; bound review app to free port {port} instead.")
    print(f"Bellhaven Sync Review App running at http://{host}:{port}/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down review server.")
    finally:
        server.server_close()
