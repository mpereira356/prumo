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

document.querySelectorAll('[data-schedule-dialog]').forEach(button => {
  button.addEventListener('click', () => {
    const dialog = document.getElementById(button.dataset.scheduleDialog);
    if (dialog && !dialog.open) dialog.showModal();
  });
});
document.querySelectorAll('.schedule-dialog').forEach(dialog => {
  dialog.querySelector('[data-dialog-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
  });
});
function openScheduleFromHash() {
  const id = location.hash.slice(1);
  if (!/^schedule-\d+$/.test(id)) return;
  const dialog = document.getElementById(id);
  if (dialog?.matches('dialog.schedule-dialog') && !dialog.open) dialog.showModal();
}
openScheduleFromHash();
window.addEventListener('hashchange', openScheduleFromHash);

const paymentForm = document.getElementById('combined-payment');
if (paymentForm) {
  const installment = document.getElementById('payment-installment');
  const amount = document.getElementById('payment-amount');
  const principal = Math.round(Number(paymentForm.dataset.principal) * 100);
  const currency = new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'});
  function paymentPreview() {
    const interest = Math.round(Number(installment.selectedOptions[0].dataset.interest) * 100);
    const value = Math.round(Number(amount.value || 0) * 100);
    const reduction = Math.min(principal, Math.max(0, value - interest));
    amount.min = (interest / 100).toFixed(2);
    amount.max = ((interest + principal) / 100).toFixed(2);
    document.getElementById('preview-interest').textContent = currency.format(interest / 100);
    document.getElementById('preview-reduction').textContent = currency.format(reduction / 100);
    document.getElementById('preview-balance').textContent = currency.format((principal - reduction) / 100);
  }
  installment.addEventListener('change', paymentPreview);
  amount.addEventListener('input', paymentPreview);
  paymentPreview();
}

const dollar = document.getElementById('dollar-quote');
if (dollar) {
  let fetching = false;
  const value = document.getElementById('dollar-value');
  const change = document.getElementById('dollar-change');
  const status = document.getElementById('dollar-status');
  const format = new Intl.NumberFormat('pt-BR', {style:'currency',currency:'BRL',minimumFractionDigits:4,maximumFractionDigits:4});
  async function refreshDollar() {
    if (fetching || document.hidden) return;
    fetching = true;
    try {
      const response = await fetch(dollar.dataset.url, {headers:{Accept:'application/json'}});
      const data = await response.json();
      if (!response.ok || !data.quote) throw new Error('unavailable');
      const bid = Number(data.quote.bid), variation = Number(data.quote.change);
      if (!Number.isFinite(bid) || !Number.isFinite(variation)) throw new Error('invalid');
      value.textContent = format.format(bid);
      change.textContent = `${variation >= 0 ? '+' : ''}${variation.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})}%`;
      change.className = variation >= 0 ? 'positive' : 'negative';
      const date = new Date(data.quote.timestamp * 1000);
      const stamp = new Intl.DateTimeFormat('pt-BR',{timeZone:'America/Sao_Paulo',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(date);
      status.textContent = `${data.stale ? 'Última cotação' : 'Cotação'}: ${stamp} · Brasília`;
      dollar.title = `Dólar comercial · compra · AwesomeAPI. ${data.stale ? 'Valor desatualizado ou provedor indisponível.' : 'Consulta a cada 5 minutos.'}`;
    } catch (_) {
      if (value.textContent === '—') status.textContent = 'Cotação indisponível';
      else status.textContent = 'Último valor · sem atualização';
    } finally { fetching = false; }
  }
  refreshDollar();
  setInterval(refreshDollar, 300000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshDollar(); });
}
