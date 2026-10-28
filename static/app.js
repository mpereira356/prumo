'use strict';
const html = document.documentElement;
try { const saved = localStorage.getItem('prumo-theme'); if (saved === 'dark' || saved === 'light') html.dataset.theme = saved; } catch (_) { /* Preferência opcional. */ }
document.getElementById('theme')?.addEventListener('click', () => {
  html.dataset.theme = html.dataset.theme === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem('prumo-theme', html.dataset.theme); } catch (_) {}
});
const menu = document.querySelector('.mobile-menu');
menu?.addEventListener('click', () => {
  const open = document.getElementById('sidebar').classList.toggle('open');
  menu.setAttribute('aria-expanded', String(open));
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') { document.getElementById('sidebar')?.classList.remove('open'); menu?.setAttribute('aria-expanded', 'false'); } });
document.addEventListener('click', e => { const side = document.getElementById('sidebar'); if (side?.classList.contains('open') && !side.contains(e.target) && !menu?.contains(e.target)) { side.classList.remove('open'); menu?.setAttribute('aria-expanded', 'false'); } });
document.querySelectorAll('form').forEach(form => form.addEventListener('submit', e => {
  const message = e.submitter?.dataset.confirm || form.dataset.confirm;
  if (message && !window.confirm(message)) { e.preventDefault(); return; }
  if (form.method.toLowerCase() === 'post') {
    if (form.dataset.submitting) { e.preventDefault(); return; }
    form.dataset.submitting = '1';
    // Não desabilita campos antes do navegador serializar o formulário.
    setTimeout(() => form.querySelectorAll('button').forEach(b => { b.disabled = true; }), 0);
  }
}));
window.addEventListener('pageshow', () => document.querySelectorAll('form').forEach(form => { delete form.dataset.submitting; form.querySelectorAll('button').forEach(b => { b.disabled = false; }); }));
const donut = document.querySelector('[data-chart="allocation"]');
if (donut) {
  const rows = [...document.querySelectorAll('.legend-row')];
  const total = rows.reduce((sum, r) => sum + Number(r.dataset.value), 0);
  let position = 0;
  const segments = rows.map(r => { const start = position; position += total ? Number(r.dataset.value) / total * 100 : 0; const color = getComputedStyle(r.querySelector('.legend-dot')).backgroundColor; return `${color} ${start}% ${position}%`; });
  // Estilo calculado apenas de números e cores locais, sem HTML dinâmico.
  if (total > 0) donut.style.background = `conic-gradient(${segments.join(',')})`;
}
const type = document.getElementById('contract-type');
function showCheque() { const field = document.getElementById('cheque-field'); if (field) field.hidden = type.value !== 'CHEQUES'; }
type?.addEventListener('change', showCheque);
if (type) showCheque();

const purchase = document.getElementById('asset-purchase');
if (purchase?.dataset.editing === '0') {
  const format = new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'});
  const available = Math.round(Number(purchase.dataset.balance)*100);
  function updatePurchase() {
    const cost = Math.round(Number(purchase.elements.qtd.value || 0)*Number(purchase.elements.pm.value || 0)*100);
    document.getElementById('purchase-cost').textContent = format.format(cost/100);
    const remaining = document.getElementById('purchase-remaining');
    remaining.textContent = format.format((available-cost)/100);
    remaining.className = cost>available ? 'negative' : 'positive';
    document.getElementById('purchase-message').textContent = cost>available ? 'Saldo insuficiente. Adicione saldo à Carteira para investir esse valor.' : 'Este valor será descontado da sua Carteira ao salvar.';
    purchase.querySelector('button[type="submit"], .form-actions button').disabled = cost<=0 || cost>available;
  }
  purchase.addEventListener('input',updatePurchase);
  window.addEventListener('pageshow',updatePurchase);
  updatePurchase();
}

function placeUndoToast(dialog) {
  const toast = document.getElementById('undo-toast');
  if (toast) dialog.appendChild(toast);
}

document.querySelectorAll('[data-schedule-dialog]').forEach(button => {
  button.addEventListener('click', () => {
    const dialog = document.getElementById(button.dataset.scheduleDialog);
    if (dialog && !dialog.open) { placeUndoToast(dialog); dialog.showModal(); }
  });
});
document.querySelectorAll('.schedule-dialog').forEach(dialog => {
  dialog.querySelectorAll('[data-dialog-close], [data-dialog-cancel]').forEach(button => button.addEventListener('click', () => dialog.close()));
  dialog.addEventListener('close', () => {
    const toast = dialog.querySelector('#undo-toast');
    if (toast) document.body.appendChild(toast);
    const opener = [...document.querySelectorAll('[data-schedule-dialog]')].find(button => button.dataset.scheduleDialog === dialog.id);
    opener?.focus({preventScroll:true});
  });
  dialog.addEventListener('click', event => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
  });
});
function openScheduleFromHash() {
  const id = location.hash.slice(1);
  if (!/^(schedule-\d+|asset-alert-\d+|settings-account|settings-telegram)$/.test(id)) return;
  const dialog = document.getElementById(id.startsWith('settings-') ? 'settings-dialog' : id);
  if (id.startsWith('settings-')) setSettingsTab(id === 'settings-telegram' ? 'telegram' : 'account');
  document.querySelectorAll('dialog[open]').forEach(other => { if (other!==dialog) other.close(); });
  if (dialog?.matches('dialog.schedule-dialog') && !dialog.open) { placeUndoToast(dialog); dialog.showModal(); }
}
document.querySelectorAll('dialog[data-auto-open]').forEach(dialog => { placeUndoToast(dialog); dialog.showModal(); });
openScheduleFromHash();
window.addEventListener('hashchange', openScheduleFromHash);

function setSettingsTab(name) {
  document.querySelectorAll('.settings-tabs [role="tab"]').forEach(tab => {
    const selected = tab.dataset.settingsTab === name;
    tab.setAttribute('aria-selected',String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  document.getElementById('settings-account-panel')?.toggleAttribute('hidden',name!=='account');
  document.getElementById('settings-telegram-panel')?.toggleAttribute('hidden',name!=='telegram');
}
document.querySelectorAll('[data-settings-tab]').forEach(button => button.addEventListener('click',()=>setSettingsTab(button.dataset.settingsTab)));
document.querySelector('.settings-tabs')?.addEventListener('keydown',event => {
  if (event.key==='ArrowLeft' || event.key==='ArrowRight') {
    const name = document.getElementById('tab-account').getAttribute('aria-selected')==='true' ? 'telegram' : 'account';
    setSettingsTab(name);
    document.getElementById(`tab-${name}`).focus();
    event.preventDefault();
  }
});

const undoToast = document.getElementById('undo-toast');
if (undoToast) {
  const progress = document.getElementById('undo-progress');
  const countdown = document.getElementById('undo-countdown');
  const navigation = performance.getEntriesByType('navigation')[0];
  const elapsed = navigation ? Math.max(0,performance.now()-navigation.responseStart) : 0;
  const deadline = performance.now() + Math.max(0,Number(undoToast.dataset.remainingMs)-elapsed);
  let submitting = false;
  document.getElementById('undo-form').addEventListener('submit', () => { submitting = true; });
  function updateUndo() {
    if (submitting) return;
    const remaining = Math.max(0,deadline-performance.now());
    progress.value = remaining;
    countdown.textContent = String(Math.ceil(remaining/1000));
    if (remaining <= 0) { undoToast.remove(); return; }
    requestAnimationFrame(updateUndo);
  }
  requestAnimationFrame(updateUndo);
  window.addEventListener('pageshow',event => { if (event.persisted) updateUndo(); });
}

const paymentForm = document.getElementById('combined-payment');
if (paymentForm) {
  const installment = document.getElementById('payment-installment');
  const amount = document.getElementById('payment-amount');
  const principal = Math.round(Number(paymentForm.dataset.principal) * 100);
  const paidDate = paymentForm.querySelector('[name="paid_on"]');
  const currency = new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'});
  function paymentPreview() {
    const interest = Math.round(Number(installment.selectedOptions[0].dataset.interest) * 100);
    const due = Date.parse(installment.selectedOptions[0].dataset.due + 'T00:00:00Z');
    const paid = Date.parse(paidDate.value + 'T00:00:00Z');
    const days = Number.isFinite(paid) ? Math.max(0,Math.floor((paid-due)/86400000)) : 0;
    const fine = days ? Math.round(interest * Number(paymentForm.dataset.multa) / 100) : 0;
    const lateInterest = days ? Math.round(interest * Number(paymentForm.dataset.mora) * days / 3000) : 0;
    const minimum = interest + fine + lateInterest;
    const value = Math.round(Number(amount.value || 0) * 100);
    const reduction = Math.min(principal, Math.max(0, value - minimum));
    amount.min = (minimum / 100).toFixed(2);
    amount.max = ((minimum + principal) / 100).toFixed(2);
    document.getElementById('preview-interest').textContent = currency.format(interest / 100);
    document.getElementById('preview-fine').textContent = currency.format(fine / 100);
    document.getElementById('preview-late-interest').textContent = currency.format(lateInterest / 100);
    document.getElementById('preview-reduction').textContent = currency.format(reduction / 100);
    document.getElementById('preview-balance').textContent = currency.format((principal - reduction) / 100);
  }
  installment.addEventListener('change', paymentPreview);
  amount.addEventListener('input', paymentPreview);
  paidDate.addEventListener('change', paymentPreview);
  paymentPreview();
}

const dollar = document.getElementById('dollar-quote');
if (dollar) {
  let fetching = false, retryTimer;
  const value = document.getElementById('dollar-value');
  const change = document.getElementById('dollar-change');
  const status = document.getElementById('dollar-status');
  const format = new Intl.NumberFormat('pt-BR', {style:'currency',currency:'BRL',minimumFractionDigits:4,maximumFractionDigits:4});
  async function refreshDollar() {
    if (fetching || document.hidden) return;
    fetching = true;
    clearTimeout(retryTimer);
    let delay = 300000;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(dollar.dataset.url, {headers:{Accept:'application/json'},cache:'no-store',signal:controller.signal});
      const data = await response.json();
      delay = data.retry_after === 60 ? 60000 : 300000;
      if (response.status === 401) { status.textContent = 'Sessão expirada · entre novamente'; return; }
      if (!response.ok || !data.quote) throw new Error('unavailable');
      const bid = Number(data.quote.bid);
      const variation = data.quote.change == null ? null : Number(data.quote.change);
      if (!Number.isFinite(bid) || bid <= 0 || (variation != null && !Number.isFinite(variation))) throw new Error('invalid');
      value.textContent = format.format(bid);
      change.hidden = variation == null;
      change.textContent = variation == null ? '' : `${variation >= 0 ? '+' : ''}${variation.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})}%`;
      change.className = variation != null && variation < 0 ? 'negative' : 'positive';
      const date = new Date(data.quote.timestamp * 1000);
      const source = data.source || data.quote.source || 'AwesomeAPI';
      if (data.quote.kind === 'daily') {
        const stamp = new Intl.DateTimeFormat('pt-BR',{timeZone:'UTC',day:'2-digit',month:'2-digit',year:'numeric'}).format(date);
        status.textContent = `${data.stale ? 'Última referência' : 'Referência diária'}: ${stamp}`;
        dollar.title = `Fonte: ${source}. Referência diária do BCE, sem variação intradiária. A fonte principal está temporariamente indisponível.`;
      } else {
        const stamp = new Intl.DateTimeFormat('pt-BR',{timeZone:'America/Sao_Paulo',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(date);
        status.textContent = `${data.stale ? 'Última cotação' : 'Cotação'}: ${stamp} · Brasília`;
        dollar.title = `Dólar comercial · compra · ${source}. ${data.stale ? 'Último valor salvo; aguardando atualização.' : 'Consulta a cada 5 minutos.'}`;
      }
    } catch (_) {
      delay = 60000;
      status.textContent = value.textContent === '—' ? 'Consultas indisponíveis · nova tentativa em 1 min' : 'Último valor · nova tentativa em 1 min';
    } finally {
      clearTimeout(timeout);
      fetching = false;
      retryTimer = setTimeout(refreshDollar, delay);
    }
  }
  refreshDollar();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshDollar(); });
}

