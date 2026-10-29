// FlowForge arcade layer for the dark theme: a starfield that flies faster the more of the order path is
// working, a red alert when the kill switch or a halt stops an agent, a scrolling mission log made of the
// crew's real status lines, and a cheer when today's journal really gains a signal, an order or a fill.
// Nothing here is invented: every effect is driven by /api/crew (agents.js passes each poll on). Read only.
(() => {
  'use strict';
  const root = document.documentElement;
  const sky = document.getElementById('space');
  const deck = document.getElementById('deck');
  const ticker = document.getElementById('ticker');
  const cheer = document.getElementById('cheer');
  if (!sky || !deck) return;
  const still = matchMedia('(prefers-reduced-motion: reduce)');
  const dark = () => root.dataset.theme === 'dark';

  // ---------- starfield ----------
  const ctx = sky.getContext('2d');
  let w = 0, h = 0, dpr = 1, stars = [], warp = 0.15, target = 0.15, red = 0, redTarget = 0;
  let mx = 0, my = 0, shoot = null, nextShoot = 0, ship = null, nextShip = 0, last = 0;
  const LAYERS = [{ n: 90, size: 0.7, speed: 0.012 }, { n: 55, size: 1.2, speed: 0.03 }, { n: 22, size: 1.9, speed: 0.07 }];
  const TINTS = ['#e8f1fb', '#a5f3fc', '#c4b5fd', '#fde68a'];

  function size() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = sky.clientWidth; h = sky.clientHeight;
    sky.width = Math.round(w * dpr); sky.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const area = Math.max(0.35, (w * h) / (1440 * 900));
    stars = [];
    LAYERS.forEach((l, z) => {
      for (let i = 0; i < Math.round(l.n * area); i++) {
        stars.push({ x: Math.random() * w, y: Math.random() * h, z, r: l.size * (0.7 + Math.random() * 0.6),
          tw: Math.random() * Math.PI * 2, c: TINTS[Math.random() < 0.8 ? 0 : 1 + Math.floor(Math.random() * 3)] });
      }
    });
  }

  function drawShip(s, dt) {
    s.x += s.v * dt; s.y += Math.sin(s.x / 90) * 0.4;
    ctx.save();
    ctx.translate(s.x, s.y);
    ctx.fillStyle = 'rgba(251, 191, 36, .9)'; // flame
    const f = 6 + Math.random() * 6;
    ctx.beginPath(); ctx.moveTo(-10, -3); ctx.lineTo(-10 - f, 0); ctx.lineTo(-10, 3); ctx.fill();
    ctx.fillStyle = '#e8f1fb';
    ctx.beginPath(); ctx.moveTo(14, 0); ctx.lineTo(-10, -7); ctx.lineTo(-6, 0); ctx.lineTo(-10, 7); ctx.closePath(); ctx.fill();
    ctx.fillStyle = '#22d3ee';
    ctx.beginPath(); ctx.arc(3, 0, 2.4, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
    return s.x < w + 40;
  }

  function frame(t) {
    requestAnimationFrame(frame);
    const dt = Math.min(48, t - (last || t)); last = t;
    if (!dark() || document.hidden) return;
    warp += (target - warp) * 0.03; red += (redTarget - red) * 0.05;
    ctx.clearRect(0, 0, w, h);
    const px = (mx - 0.5) * 18, py = (my - 0.5) * 12;
    for (const s of stars) {
      const l = LAYERS[s.z], v = l.speed * (1 + warp * 14);
      s.x -= v * dt; s.tw += dt * 0.003;
      if (s.x < -20) { s.x = w + 20; s.y = Math.random() * h; }
      const x = s.x + px * (s.z + 1) * 0.5, y = s.y + py * (s.z + 1) * 0.5;
      const a = 0.45 + 0.4 * Math.sin(s.tw) + s.z * 0.1;
      ctx.globalAlpha = Math.max(0.15, Math.min(1, a));
      ctx.fillStyle = red > 0.05 && s.z === 2 ? `rgba(251, 113, 133, ${0.4 + red * 0.6})` : s.c;
      const streak = s.z ? Math.min(6 + s.z * 6, v * 40 * warp) : 0; // short dashes, never long bars
      if (streak > 1.5) {
        ctx.strokeStyle = ctx.fillStyle; ctx.lineWidth = s.r;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + streak, y); ctx.stroke();
      } else {
        ctx.beginPath(); ctx.arc(x, y, s.r, 0, Math.PI * 2); ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
    if (t > nextShoot) { // a shooting star now and then
      shoot = { x: Math.random() * w * 0.8 + w * 0.2, y: Math.random() * h * 0.4, life: 1 };
      nextShoot = t + 5000 + Math.random() * 9000;
    }
    if (shoot) {
      shoot.x -= dt * 0.9; shoot.y += dt * 0.35; shoot.life -= dt / 900;
      const g = ctx.createLinearGradient(shoot.x, shoot.y, shoot.x + 90, shoot.y - 35);
      g.addColorStop(0, `rgba(255, 255, 255, ${Math.max(0, shoot.life)})`); g.addColorStop(1, 'rgba(255, 255, 255, 0)');
      ctx.strokeStyle = g; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(shoot.x, shoot.y); ctx.lineTo(shoot.x + 90, shoot.y - 35); ctx.stroke();
      if (shoot.life <= 0) shoot = null;
    }
    if (!ship && target > 0.3 && t > nextShip) { // the crew's ship flies past while the order path works
      ship = { x: -40, y: h * (0.2 + Math.random() * 0.6), v: 0.12 + target * 0.18 };
      nextShip = t + 14000 + Math.random() * 12000;
    }
    if (ship && !drawShip(ship, dt)) ship = null;
  }

  // ---------- cheers: only when today's journal really grows ----------
  let tally = null;
  function cardOf(id) { return deck.querySelector(`.agent[data-id="${id}"]`); }

  function toast(text, cls) {
    if (!cheer) return;
    const el = document.createElement('div');
    el.className = `cheer-msg ${cls}`;
    el.textContent = text;
    cheer.appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  function burst(id) { // confetti from an agent's card
    const card = cardOf(id);
    if (!card || still.matches) return;
    const box = document.createElement('div');
    box.className = 'confetti';
    const colors = ['#22d3ee', '#a78bfa', '#34d399', '#fbbf24', '#fb7185', '#f0abfc'];
    for (let i = 0; i < 28; i++) {
      const p = document.createElement('i');
      const a = (Math.PI * 2 * i) / 28 + Math.random() * 0.3, d = 50 + Math.random() * 70;
      p.style.setProperty('--dx', `${Math.cos(a) * d}px`);
      p.style.setProperty('--dy', `${Math.sin(a) * d - 30}px`);
      p.style.setProperty('--rot', `${Math.random() * 720 - 360}deg`);
      p.style.background = colors[i % colors.length];
      box.appendChild(p);
    }
    card.appendChild(box);
    setTimeout(() => box.remove(), 1600);
  }

  function pop(id, cls) {
    const card = cardOf(id);
    if (!card) return;
    card.classList.remove(cls); void card.offsetWidth; card.classList.add(cls);
    setTimeout(() => card.classList.remove(cls), 1400);
  }

  function celebrate(prev, now) {
    if (!dark()) return;
    if (now.fill > prev.fill) { burst('executor'); pop('executor', 'cheered'); toast('★ Order filled! (paper money) ★', 'win'); }
    else if (now.order_submit > prev.order_submit) { pop('executor', 'launch'); toast('Order sent to the broker', 'go'); }
    if (now.signal > prev.signal) { pop('strategist', 'idea'); toast('The Strategist spotted a setup', 'idea'); }
  }

  // ---------- mission log: the crew's real status lines, scrolling ----------
  let logText = '';
  function mission(c) {
    if (!ticker) return;
    const text = c.agents.map((a) => `${a.name}: ${a.detail}`).join('   ★   ');
    if (text === logText) return;
    logText = text;
    const span = () => { const s = document.createElement('span'); s.textContent = `★   ${text}   `; return s; };
    const run = document.createElement('div');
    run.className = 'run';
    run.style.setProperty('--secs', `${Math.max(30, text.length / 6)}s`);
    run.append(span(), span());
    ticker.replaceChildren(run);
  }

  function onCrew(c) {
    const chain = c.agents.filter((a) => c.chain.includes(a.id));
    const working = chain.filter((a) => a.state === 'working').length;
    const blocked = c.agents.some((a) => a.state === 'blocked');
    target = 0.08 + (working / Math.max(1, chain.length)) * 0.9;
    redTarget = blocked ? 1 : 0;
    root.classList.toggle('red-alert', blocked);
    mission(c);
    if (c.tally) {
      if (tally) celebrate(tally, c.tally);
      tally = c.tally;
    }
  }

  // a robot says what it does when tapped (agents.js fires flowforge:say)
  function onSay(e) {
    const { id } = e.detail;
    pop(id, 'hop');
  }

  // the crew waves hello once when the page opens in the dark theme
  function hello() {
    if (!dark() || still.matches) return;
    deck.querySelectorAll('.agent').forEach((el, i) => setTimeout(() => pop(el.dataset.id, 'hop'), 200 + i * 120));
  }

  window.addEventListener('flowforge:crew', (e) => onCrew(e.detail));
  window.addEventListener('flowforge:say', onSay);
  window.addEventListener('flowforge:theme', hello);
  window.addEventListener('resize', size);
  window.addEventListener('pointermove', (e) => { mx = e.clientX / Math.max(1, w); my = e.clientY / Math.max(1, h); }, { passive: true });
  window.FlowForgeArcade = { burst, toast, pop }; // for walkthroughs and screenshots only; nothing calls it on a timer

  size();
  if (still.matches) { // one calm, still frame
    target = warp = 0; last = 0;
    const draw = () => { if (!dark()) return; ctx.clearRect(0, 0, w, h); stars.forEach((s) => { ctx.globalAlpha = 0.6; ctx.fillStyle = s.c; ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2); ctx.fill(); }); };
    draw();
    window.addEventListener('flowforge:theme', draw);
  } else {
    requestAnimationFrame(frame);
  }
  let greeted = false;
  window.addEventListener('flowforge:crew', () => { if (!greeted) { greeted = true; setTimeout(hello, 100); } });
})();
