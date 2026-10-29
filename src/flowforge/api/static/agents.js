// FlowForge agent deck. One animated avatar per part of the bot, drawn from /api/crew, which reads the
// same files as the Health page. An avatar moves only while its part is really working. Read only.
(() => {
  'use strict';
  const deck = document.getElementById('deck');
  const box = document.getElementById('agents');
  const count = document.getElementById('deck-count');
  const say = document.getElementById('say');
  if (!deck || !box) return;

  // Each glyph is drawn inside the avatar shell (a 56px square, centre 28,28), keyed by /api/crew ids.
  const GLYPH = {
    scout: 'M20 32l5-7 4 4 7-9',
    tape: 'M20 34v-6M25 34v-11M30 34v-8M35 34v-13',
    strategist: 'M28 19v4M28 33v4M19 28h4M33 28h4M28 25a3 3 0 1 0 0 6a3 3 0 1 0 0-6',
    jev: 'M21 28c3-5 11-5 14 0c-3 5-11 5-14 0zM28 26.5v3',
    guard: 'M28 19l7 3v6c0 4-3 7-7 9c-4-2-7-5-7-9v-6z',
    executor: 'M21 28h12M29 23l5 5-5 5',
    watchdog: 'M21 24l3-4 2 3h4l2-3 3 4v6c0 3-3 6-7 6s-7-3-7-6z',
    herald: 'M24 30a6 6 0 0 1 0-6M21 33a10 10 0 0 1 0-12M32 30a6 6 0 0 0 0-6M35 33a10 10 0 0 0 0-12M28 27v.5',
    archivist: 'M22 20h12v16H22zM25 25h6M25 29h6',
  };
  // What each robot says when tapped: its job, in plain words, and then how it is right now.
  const JOB = {
    scout: 'I listen to every trade in the market, super fast!',
    tape: 'I count who is buying and who is selling.',
    strategist: 'I hunt for patterns that might make a good trade.',
    jev: "I'm the AI. I double-check each idea, and I have to answer in under a second!",
    guard: 'I protect the money. If a trade is too risky, I say NO.',
    executor: 'I send the orders to the broker. Practice money only!',
    watchdog: 'If anything goes wrong, I sell everything and stop the bot.',
    herald: 'I send messages to your phone.',
    archivist: 'I write down everything that happens.',
  };
  const NOW = { working: "I'm working right now!", ready: "I'm ready and waiting.", blocked: "I'm stopped, to keep the money safe.", off: "I'm asleep: the bot isn't running." };
  // API state -> avatar class and label
  const LOOK = { working: ['working', 'Working'], ready: ['idle', 'Standby'], blocked: ['alert', 'Blocked'], off: ['offline', 'Offline'] };

  const avatar = (id) => `<svg class="bot" viewBox="0 0 56 56" aria-hidden="true">
      <circle class="halo" cx="28" cy="28" r="26"/>
      <g class="orbit"><circle cx="28" cy="2" r="2.6"/><circle cx="54" cy="28" r="1.8"/></g>
      <rect class="shell" x="13" y="13" width="30" height="30" rx="9"/>
      <circle class="core" cx="28" cy="28" r="2.2"/>
      <path class="glyph" d="${GLYPH[id] || ''}"/>
      <path class="eye" d="M22 16.5h12"/>
      <g class="antenna"><path d="M28 13V7"/><circle cx="28" cy="5.5" r="2.2"/></g>
      <g class="eyes"><ellipse cx="23" cy="17.6" rx="1.9" ry="2"/><ellipse cx="33" cy="17.6" rx="1.9" ry="2"/></g>
      <path class="jet" d="M22 44.5q2 6 3.2 9l2.8-3.5 2.8 3.5q1.2-3 3.2-9z"/>
      <g class="zzz"><text x="40" y="12">z</text><text x="45" y="6">z</text></g>
    </svg>`;

  // Cards are built once; later polls only change classes and text, so the animations run on smoothly.
  const cards = {};
  function card(a) {
    const el = document.createElement('div');
    el.className = 'agent offline';
    el.dataset.id = a.id;
    el.tabIndex = 0;
    el.setAttribute('role', 'button');
    el.addEventListener('click', () => talk(a.id));
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); talk(a.id); } });
    el.innerHTML = `${avatar(a.id)}<div class="name"></div><div class="role"></div><span class="state"></span><div class="detail"></div>`;
    el.querySelector('.name').textContent = a.name;
    el.querySelector('.role').textContent = a.role;
    box.appendChild(el);
    return (cards[a.id] = el);
  }

  let hush = 0;
  function talk(id) {
    const el = cards[id];
    if (!el || !say) return;
    say.textContent = `${el.querySelector('.name').textContent}: "${JOB[id] || ''} ${NOW[el.dataset.state] || ''}"`;
    say.hidden = false;
    clearTimeout(hush);
    hush = setTimeout(() => { say.hidden = true; }, 7000);
    window.dispatchEvent(new CustomEvent('flowforge:say', { detail: { id } }));
  }

  function show(a) {
    const el = cards[a.id] || card(a);
    const [cls, label] = LOOK[a.state] || LOOK.off;
    el.className = `agent ${cls}${el.className.match(/ (hop|cheered|launch|idea)\b/g)?.join('') || ''}`;
    el.dataset.state = a.state;
    el.querySelector('.state').textContent = label;
    el.querySelector('.detail').textContent = a.detail;
    el.title = `${a.name}: ${label}. ${a.detail} (tap to hear what I do)`;
    el.setAttribute('aria-label', `${a.name}, ${a.role}: ${label}. ${a.detail}`);
  }

  async function poll() {
    try {
      const r = await fetch('/api/crew', { cache: 'no-store' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const c = await r.json();
      c.agents.forEach(show);
      count.textContent = c.line;
      // packets run along the order path only while one of its agents is working
      deck.classList.toggle('quiet', !c.agents.some((a) => c.chain.includes(a.id) && a.state === 'working'));
      window.dispatchEvent(new CustomEvent('flowforge:crew', { detail: c }));
    } catch (err) {
      count.textContent = `Lost the link to the dashboard: ${err.message}`;
      deck.classList.add('quiet');
    }
  }

  poll();
  setInterval(poll, 5000);
})();
