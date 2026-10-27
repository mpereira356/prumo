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
