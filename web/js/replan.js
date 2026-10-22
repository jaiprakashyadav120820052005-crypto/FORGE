/**
 * Pivot A re-plan panel: "a pick sold out and the budget dropped".
 * Preview -> (server keeps the proposal) -> Accept / Discard -> Undo. Nothing changes until Accept.
 * All text that came from the web or the catalog goes through escapeHtml (defined in app.js).
 */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (v) => (typeof escapeHtml === 'function' ? escapeHtml(v) : String(v ?? ''));
  const inr = (n) => '₹' + Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 });
  const post = (url, body) => api.req(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const msg = (t) => { $('replan-message').textContent = t || ''; };

  async function renderSoldOutPicker() {
    const bom = await api.getBom();
    const parts = Object.values(bom.parts || {}).filter((p) => p && !p.none);
    $('replan-soldout').innerHTML = parts.length
      ? '<b style="width:100%">What sold out?</b>' + parts.map((p) =>
          `<label><input type="checkbox" class="replan-so" value="${esc(p.id)}"> ${esc(p.name)}</label>`).join('')
      : '<span class="caption">Generate a BOM first.</span>';
  }

  function change(c) {
    const f = c.from_part, t = c.to_part;
    const kind = c.kind === 'vendor_switch' ? 'Same part, different shop' : 'Part swap';
    const d = c.cost_delta || 0;
    const delta = d === 0 ? 'no price change' : (d < 0 ? 'saves ' : 'costs ') + inr(Math.abs(d));
    const why = (c.reasons || []).map((r) =>
      `<div><span class="replan-badge ${r.code === 'sold_out' ? 'bad' : 'warn'}">${r.code === 'sold_out' ? 'Sold out' : 'Budget'}</span>${esc(r.text)}</div>`).join('');
    const q = c.data_quality ? `<div class="caption">Prices: ${esc(c.data_quality.old)} → ${esc(c.data_quality.new)}${
      (c.data_quality.old === 'seed' || c.data_quality.new === 'seed') ? ' (seed = team estimate, not yet verified on a shop page)' : ''}</div>` : '';
    const link = c.source_url ? `<div class="caption"><a href="${esc(c.source_url)}" target="_blank" rel="noopener noreferrer">Open the in-stock listing</a></div>` : '';
    return `<div class="replan-change">
      <div><b>${esc(c.role)}</b> · ${kind} · ${delta}</div>
      <div>${esc(f.name)} <span class="arrow">→</span> <b>${esc(t.name)}</b></div>
      ${why}
      ${(c.impact || []).length ? `<div class="replan-chips">${c.impact.map((x) => `<span>${esc(x)}</span>`).join('')}</div>` : ''}
      ${q}${link}</div>`;
  }

  function render(p) {
    const b = p.budget, g = p.guards;
    const badge = { fits: ['fits', 'Fits the new budget'], over_budget: ['bad', 'Still over budget'], partial: ['warn', 'Partly resolved'], blocked: ['bad', 'No safe plan found'] }[p.status] || ['warn', p.status];
    let html = `<p><span class="replan-badge ${badge[0]}">${badge[1]}</span></p>
      <div class="replan-summary">
        <div>Budget<b>${inr(b.old)} → ${inr(b.new)}</b><span class="caption">−${b.cut_pct}%</span></div>
        <div>Total<b>${inr(b.before_total)} → ${inr(b.after_total)}</b></div>
        <div>${b.shortfall > 0 ? 'Over by' : 'Left'}<b>${inr(b.shortfall > 0 ? b.shortfall : b.left)}</b></div>
        <div>Compatibility<b>${esc(g.compat_before.state)} → ${esc(g.compat_after.state)}</b><span class="caption">${g.no_rule_worse ? 'no rule got worse' : 'a rule got worse'}</span></div>
      </div>`;
    if (p.status === 'over_budget') html += `<p class="caption"><b>Honest result:</b> no further safe saving exists. This is the cheapest plan that keeps every check passing; ${inr(b.shortfall)} is still over. Raise the budget, drop a feature, or accept a part you choose yourself in the optimizer.</p>`;
    (p.unresolved || []).forEach((u) => {
      html += `<div class="replan-change"><span class="replan-badge bad">Needs your decision</span><b>${esc(u.role)}</b><div>${esc(u.text)}</div>${
        (u.rejected || []).length ? `<div class="caption">Rejected: ${u.rejected.map((r) => `${esc(r.name)} (${esc(r.why)})`).join('; ')}</div>` : ''}</div>`;
    });
    html += (p.changes || []).length ? '<h5>What changed and why</h5>' + p.changes.map(change).join('') : '<p class="caption">No part needs to change.</p>';
    if ((p.unchanged || []).length) html += `<p class="caption"><b>Unchanged:</b> ${p.unchanged.map(esc).join(', ')}</p>`;
    if ((g.unpriced_after || []).length) html += `<p class="caption">⚠ No price known for: ${g.unpriced_after.map(esc).join(', ')}. The total does not include them.</p>`;
    if ((g.stale || []).length) html += `<p class="caption">After you accept, the bill, ledger, wiring and firmware are rebuilt from the new parts. Re-run live sourcing to verify prices of the new parts.</p>`;
    $('replan-result').innerHTML = html;
    $('replan-result').hidden = false;
    $('replan-actions').hidden = !(p.changes || []).length && !(p.budget.new !== p.budget.old);
  }

  async function preview() {
    const ids = Array.from(document.querySelectorAll('.replan-so:checked')).map((x) => x.value);
    const cut = parseFloat($('replan-cut').value);
    if (!(cut > 0 && cut < 100)) { msg('Enter a budget cut between 1 and 99.'); return; }
    $('btn-replan-preview').disabled = true; msg('Re-planning…');
    try { render(await post('/api/replan/preview', { cut_pct: cut, sold_out_part_ids: ids })); msg(''); }
    catch (e) { msg('Could not re-plan: ' + e.message); $('replan-result').hidden = true; $('replan-actions').hidden = true; }
    finally { $('btn-replan-preview').disabled = false; }
  }

  async function accept() {
    $('btn-replan-accept').disabled = true;
    try {
      const r = await post('/api/replan/accept');
      if (window.forgeRenderBom) window.forgeRenderBom(r.bom);
      $('replan-result').hidden = true; $('replan-actions').hidden = true; $('btn-replan-undo').hidden = false;
      msg('Re-plan applied. Bill, wiring and compatibility now use the updated build. Run live sourcing to verify the new parts’ prices.');
      renderSoldOutPicker();
    } catch (e) { msg('Could not apply: ' + e.message); }
    finally { $('btn-replan-accept').disabled = false; }
  }

  async function discard() { await post('/api/replan/discard').catch(() => {}); $('replan-result').hidden = true; $('replan-actions').hidden = true; msg('Discarded. Nothing was changed.'); }

  async function undo() {
    try { const r = await post('/api/replan/undo'); if (window.forgeRenderBom) window.forgeRenderBom(r.bom); msg('Last re-plan undone.'); $('btn-replan-undo').hidden = true; renderSoldOutPicker(); }
    catch (e) { msg('Could not undo: ' + e.message); }
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('btn-toggle-replan')?.addEventListener('click', async () => {
      const open = $('replan-content').hidden;
      $('replan-content').hidden = !open;
      $('btn-toggle-replan').setAttribute('aria-expanded', String(open));
      $('btn-toggle-replan').textContent = open ? 'Close re-plan' : 'Open re-plan';
      if (open) { try { await renderSoldOutPicker(); } catch (e) { msg(e.message); } }
    });
    $('btn-replan-preview')?.addEventListener('click', preview);
    $('btn-replan-accept')?.addEventListener('click', accept);
    $('btn-replan-discard')?.addEventListener('click', discard);
    $('btn-replan-undo')?.addEventListener('click', undo);
  });
})();
