(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const root = document.documentElement;
  const storeKey = 'onepathConcept';
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(storeKey) || '{}') || {}; } catch { saved = {}; }

  const agents = {
    Gandalf: {
      kicker: 'ORCHESTRATOR · CLOUD ONLY',
      quotes: ['A clear path begins with a clear question.', 'Let the next step be small enough to begin.', 'Bring the moving pieces into view.'],
      credit: 'GANDALF'
    },
    Hermes: {
      kicker: 'HERMES · RESEARCH',
      quotes: ['Curiosity turns a map into a journey.', 'Follow the question until the useful detail appears.', 'A good answer starts with careful listening.'],
      credit: 'HERMES'
    },
    Atomic: {
      kicker: 'ATOMIC · WORKFLOWS',
      quotes: ['A steady rhythm makes room for deep work.', 'Make the next useful action easy to repeat.', 'Small systems carry big intentions.'],
      credit: 'ATOMIC'
    },
    DeepSeek: {
      kicker: 'DEEPSEEK · CODE',
      quotes: ['Trace the signal; simplify the noise.', 'One precise change can clarify the whole system.', 'Build, observe, and follow the evidence.'],
      credit: 'DEEPSEEK'
    }
  };

  let selectedAgent = agents[saved.agent] ? saved.agent : 'Gandalf';
  let messageMode = 'greeting';
  let quoteIndex = 0;
  let use24Hour = saved.clock24 !== false;
  let uiScale = Math.max(.8, Math.min(1.2, Number(saved.scale) || 1));
  root.style.setProperty('--ui-scale', String(uiScale));
  let focusTimer = null;
  let focusSeconds = 25 * 60;
  let poseTimer = null;
  let messageTimer = null;
  let currentPose = '';
  let toastTimer = null;

  const persist = () => {
    const data = {
      theme: root.dataset.theme || 'mint',
      motion: !root.classList.contains('no-motion'),
      crt: root.classList.contains('crt-enabled'),
      scale: uiScale,
      agent: selectedAgent,
      clock24: use24Hour,
      headerHidden: $('#topBar').classList.contains('header-hidden'),
      displayName: $('#displayName').value.trim()
    };
    try { localStorage.setItem(storeKey, JSON.stringify(data)); } catch { /* Private browsing may disable storage. */ }
  };

  const toast = (message) => {
    const node = $('#toast');
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('show'), 1900);
  };

  const greeting = (date = new Date()) => {
    const hour = date.getHours();
    const part = hour >= 5 && hour < 12 ? 'morning' : hour >= 12 && hour < 17 ? 'afternoon' : hour >= 17 ? 'evening' : 'night';
    const name = $('#displayName').value.trim();
    return name ? `Good ${part}, ${name}.` : `Good ${part}.`;
  };

  const paintMessage = (animate = true) => {
    const slot = $('#messageSlot');
    const paint = () => {
      const config = agents[selectedAgent];
      if (messageMode === 'greeting') {
        $('#messageKicker').textContent = 'A MOMENT FOR YOU';
        $('#messageText').textContent = greeting();
        $('#messageCredit').textContent = '';
      } else {
        const quote = config.quotes[quoteIndex % config.quotes.length];
        $('#messageKicker').textContent = config.kicker;
        $('#messageText').textContent = `“${quote}”`;
        $('#messageCredit').textContent = config.credit;
      }
    };
    if (!animate) { paint(); return; }
    slot.classList.add('is-fading');
    setTimeout(() => { paint(); slot.classList.remove('is-fading'); }, 220);
  };

  const drawClock = () => {
    const now = new Date();
    const hour24 = now.getHours();
    const marker = hour24 < 12 ? 'AM' : 'PM';
    const hour = use24Hour ? hour24 : (hour24 % 12 || 12);
    const hh = String(hour).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    $('#timeHours').textContent = hh;
    $('#timeMinutes').textContent = mm;
    $('#clock').textContent = use24Hour ? `${hh}:${mm}` : `${hh}:${mm} ${marker}`;
    const toggleLabel = use24Hour ? 'Switch to 12-hour clock' : 'Switch to 24-hour clock';
    $('#clockFormat').textContent = use24Hour ? '24H' : marker;
    $('#clockFormat').setAttribute('aria-label', toggleLabel);
    $('#clockFormat').title = `${toggleLabel} · click to switch`;
    $('#clockFormatSetting').textContent = `${use24Hour ? '24H' : '12H'} clock`;
    $('#clockFormatSetting').setAttribute('aria-pressed', String(use24Hour));
    $('#clockFormatSetting').title = toggleLabel;
    if (messageMode === 'greeting') $('#messageText').textContent = greeting(now);
  };

  const toggleClockFormat = () => { use24Hour = !use24Hour; drawClock(); persist(); };
  $('#clockFormat').addEventListener('click', toggleClockFormat);
  $('#clockFormatSetting').addEventListener('click', toggleClockFormat);
  $('#displayName').value = saved.displayName || '';
  $('#displayName').addEventListener('input', () => { if (messageMode === 'greeting') paintMessage(false); persist(); });
  drawClock();
  setInterval(drawClock, 1000);

  if (saved.theme) root.dataset.theme = saved.theme === 'violet' ? 'matrix' : saved.theme;
  $$('.swatch').forEach((button) => {
    button.classList.toggle('selected', button.dataset.theme === (root.dataset.theme || 'mint'));
    button.setAttribute('aria-pressed', String(button.dataset.theme === (root.dataset.theme || 'mint')));
    button.addEventListener('click', () => {
      root.dataset.theme = button.dataset.theme;
      $$('.swatch').forEach((item) => {
        const active = item === button;
        item.classList.toggle('selected', active);
        item.setAttribute('aria-pressed', String(active));
      });
      persist();
    });
  });

  const header = $('#topBar');
  const headerToggle = $('#headerToggle');
  const setHeaderVisible = (visible, notify = false) => {
    header.classList.toggle('header-hidden', !visible);
    headerToggle.textContent = visible ? '⌃' : '⌄';
    headerToggle.setAttribute('aria-expanded', String(visible));
    headerToggle.setAttribute('aria-label', visible ? 'Hide header' : 'Show header');
    headerToggle.title = visible ? 'Hide header' : 'Show header';
    persist();
    if (notify) toast(visible ? 'Header shown' : 'Header hidden');
  };
  setHeaderVisible(!saved.headerHidden);
  headerToggle.addEventListener('click', () => setHeaderVisible(header.classList.contains('header-hidden'), true));

  const agentPanel = $('#agentPanel');
  const motif = $('#skyMotif');
  let motifRequest = 0;
  const motifBase = '<svg class="motif-svg" viewBox="0 0 260 132" role="img" aria-label="Agent constellation"><path class="motif-line" d="M28 78 55 42 88 64 119 29 151 55 184 36 220 67 238 45M55 42 64 101 119 29 124 94 184 36 193 91 238 45"/><g class="motif-star"><circle cx="28" cy="78" r="2.4"/><circle cx="55" cy="42" r="2.6"/><circle cx="88" cy="64" r="1.8"/><circle cx="119" cy="29" r="2.9"/><circle cx="151" cy="55" r="1.8"/><circle cx="184" cy="36" r="2.6"/><circle cx="220" cy="67" r="2"/><circle cx="238" cy="45" r="2.4"/><circle cx="64" cy="101" r="1.7"/><circle cx="124" cy="94" r="1.6"/><circle cx="193" cy="91" r="1.8"/></g>';

  const constellationFor = (agent) => {
    let art = '';
    let label = `${agent} constellation`;
    if (agent === 'Hermes') {
      label = 'Hermes constellation with the Nous girl seal';
      art = '<g class="motif-icon"><path d="M126 74V44m-8 9c8-12 17-12 24 0-7 11-16 11-24 0Zm8-18 4-7 4 7m-15 36 7-5 7 5"/><circle cx="130" cy="37" r="2"/></g><image class="motif-avatar" href="assets/nous-stamp-light.svg" x="174" y="58" width="48" height="48"/><text class="motif-caption" x="130" y="123" text-anchor="middle">HERMES · NOUS GIRL</text>';
    } else if (agent === 'DeepSeek') {
      label = 'DeepSeek whale and broad-finned sea creature constellation';
      art = '<g class="motif-icon"><path d="M78 75c10-16 31-22 53-18 16 3 27 12 40 9l13-11 4 16-4 17-15-12c-7 8-16 14-28 16-12 2-28-1-37-8l-17 4 5-10-14 1 8-7Zm25-14-12-18 27 11 9 6m19 0 20-19-7 26m-57 10-12 17 27-10m21-5 22 16-11-20"/><circle cx="96" cy="69" r="1.8"/><path d="m104 62 9 7-9 7m43-13-8 9 10 7m-31-20 5-7m13 6 4-7" stroke="#ff546b"/><path d="m102 59 6-7 7 8m29-2 8-7 5 9" stroke="#ff546b"/></g><text class="motif-caption" x="130" y="123" text-anchor="middle">DEEPSEEK · TIDAL SIGNAL</text>';
    } else if (agent === 'Atomic') {
      label = 'Atomic atom constellation';
      art = '<g class="motif-icon"><ellipse cx="130" cy="70" rx="35" ry="12"/><ellipse cx="130" cy="70" rx="35" ry="12" transform="rotate(60 130 70)"/><ellipse cx="130" cy="70" rx="35" ry="12" transform="rotate(120 130 70)"/><circle cx="130" cy="70" r="4" fill="var(--accent)"/><circle cx="99" cy="65" r="2.5" fill="var(--accent)"/><circle cx="139" cy="38" r="2.5" fill="var(--accent)"/><circle cx="139" cy="102" r="2.5" fill="var(--accent)"/></g><text class="motif-caption" x="130" y="123" text-anchor="middle">ATOMIC · ORBITAL FLOW</text>';
    } else {
      label = 'Gandalf wizard constellation';
      art = '<g class="motif-icon"><path d="m113 61 17-31 17 31-7-5-10 7-10-7-7 5Zm8 3c-4 10-5 22-4 32h26c1-10 0-22-4-32m-25 10h32m-26 22-8 14m25-14 8 14m-14-45v45m-18-27-9 12m38-12 9 12"/><path d="M112 109h36m-18-83v-8m-8 4 8 4 8-4"/><circle cx="130" cy="72" r="2" fill="var(--accent)"/></g><text class="motif-caption" x="130" y="123" text-anchor="middle">GANDALF · THE WAYFINDER</text>';
    }
    motif.innerHTML = `${motifBase}${art}</svg>`;
    motif.setAttribute('aria-label', label);
  };

  const selectAgent = (name) => {
    if (!agents[name]) return;
    selectedAgent = name;
    $$('.agent').forEach((button) => {
      const active = button.dataset.agent === name;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    quoteIndex = 0;
    messageMode = 'agent';
    paintMessage(true);
    motif.classList.add('changing');
    const request = ++motifRequest;
    setTimeout(() => {
      if (request !== motifRequest) return;
      constellationFor(name);
      motif.classList.remove('changing');
    }, 240);
    persist();
  };
  $$('.agent').forEach((button) => button.addEventListener('click', () => selectAgent(button.dataset.agent)));
  selectAgent(selectedAgent);

  messageTimer = setInterval(() => {
    if (root.classList.contains('no-motion') || document.hidden) return;
    if (messageMode === 'greeting') { messageMode = 'agent'; }
    else { messageMode = 'greeting'; quoteIndex = (quoteIndex + 1) % agents[selectedAgent].quotes.length; }
    paintMessage(true);
  }, 9000);
  $('#messageNext').addEventListener('click', () => {
    if (messageMode === 'greeting') messageMode = 'agent';
    else { messageMode = 'greeting'; quoteIndex = (quoteIndex + 1) % agents[selectedAgent].quotes.length; }
    paintMessage(true);
  });

  const controlsToggle = $('#controlsToggle');
  const controlsMenu = $('#controlsMenu');
  const setControlsOpen = (open) => {
    controlsMenu.hidden = !open;
    controlsToggle.setAttribute('aria-expanded', String(open));
    controlsToggle.setAttribute('aria-label', open ? 'Close controls' : 'Open controls');
    controlsToggle.title = open ? 'Close controls' : 'Controls';
  };
  controlsToggle.addEventListener('click', () => setControlsOpen(controlsMenu.hidden));

  const setPanel = (panel, open) => { panel.hidden = !open; };
  $$('.menu-item').forEach((button) => button.addEventListener('click', () => {
    const action = button.dataset.action;
    if (action === 'agents') {
      const isVisible = getComputedStyle(agentPanel).display !== 'none';
      agentPanel.hidden = isVisible;
      toast(isVisible ? 'Agent picker hidden · open Controls to restore' : 'Agent picker shown');
    } else if (action === 'appearance') {
      setPanel($('#themePanel'), $('#themePanel').hidden);
    } else {
      toast(action === 'command' ? 'Command is a preview · not connected' : 'Files are a preview · not connected');
    }
  }));
  $$('.panel-close').forEach((button) => button.addEventListener('click', () => setPanel($(`#${button.dataset.close}`), false)));

  const themePanel = $('#themePanel');
  $('#crtToggle').addEventListener('click', (event) => {
    root.classList.toggle('crt-enabled');
    const enabled = root.classList.contains('crt-enabled');
    event.currentTarget.setAttribute('aria-pressed', String(enabled));
    event.currentTarget.textContent = enabled ? '▤ CRT on' : '▤ CRT off';
    persist();
  });
  $('#motionToggle').addEventListener('click', (event) => {
    root.classList.toggle('no-motion');
    const stopped = root.classList.contains('no-motion');
    event.currentTarget.setAttribute('aria-pressed', String(stopped));
    $('#motionText').textContent = stopped ? 'Motion off' : 'Motion on';
    persist();
  });
  $('#scaleSlider').value = Math.round(uiScale * 100);
  $('#scaleValue').value = `${Math.round(uiScale * 100)}%`;
  const setScale = (value) => {
    uiScale = Math.max(.8, Math.min(1.2, Number(value) / 100));
    root.style.setProperty('--ui-scale', String(uiScale));
    $('#scaleValue').value = `${Math.round(uiScale * 100)}%`;
    persist();
  };
  $('#scaleSlider').addEventListener('input', (event) => setScale(event.target.value));
  if (saved.crt) root.classList.add('crt-enabled');
  $('#crtToggle').setAttribute('aria-pressed', String(!!saved.crt));
  $('#crtToggle').textContent = saved.crt ? '▤ CRT on' : '▤ CRT off';
  if (saved.motion === false) root.classList.add('no-motion');
  $('#motionToggle').setAttribute('aria-pressed', String(saved.motion === false));
  $('#motionText').textContent = saved.motion === false ? 'Motion off' : 'Motion on';

  const poses = {
    working: '<g class="pose-art"><rect x="48" y="43" width="12" height="9" rx="1"/><path d="M46 55h17M51 52l-2 3m8-3 2 3"/><path d="M50 46h8"/></g>',
    coffee: '<g class="pose-art"><path d="M51 45h8v9h-8zM59 47h3v4h-3M49 56h13"/><path d="M53 41c-2-2 2-2 0-4m4 4c-2-2 2-2 0-4"/></g>',
    sleeping: '<g class="pose-art"><path d="M50 50h11v5H50zM48 57h16"/><text x="51" y="42" font-size="7" fill="currentColor" stroke="none">Z</text><text x="58" y="37" font-size="5" fill="currentColor" stroke="none">z</text><path d="m62 45 2-2m-2 5 3 1"/></g>',
    meditating: '<g class="pose-art"><circle cx="55" cy="40" r="3"/><path d="M55 43v6m0-3-5 2m5-2 5 2m-10 5c2-3 8-3 10 0m-13 5h16m-8-7v7"/><path d="m48 40-2-2m15 2 2-2"/></g>'
  };
  const choosePose = () => {
    const options = Object.keys(poses).filter((pose) => pose !== currentPose);
    currentPose = options[Math.floor(Math.random() * options.length)];
    $('#naviPose').innerHTML = poses[currentPose];
    $('#naviTrigger').dataset.pose = currentPose;
    $('#naviTrigger').setAttribute('aria-label', `NAVI ${currentPose} · open product shortcuts`);
  };
  const stopFocus = (completed = false) => {
    clearInterval(focusTimer);
    clearInterval(poseTimer);
    focusTimer = poseTimer = null;
    root.classList.remove('focus-active');
    $('#focusButton').setAttribute('aria-pressed', 'false');
    $('#focusText').textContent = completed ? 'Session complete' : 'Start focusing';
    $('#focusTimer').hidden = true;
    focusSeconds = 25 * 60;
    if (completed) setTimeout(() => { if (!focusTimer) $('#focusText').textContent = 'Start focusing'; }, 2500);
  };
  const updateFocusTimer = () => {
    const mins = String(Math.floor(focusSeconds / 60)).padStart(2, '0');
    const secs = String(focusSeconds % 60).padStart(2, '0');
    $('#focusTimer').textContent = `${mins}:${secs} · FOCUSING`;
  };
  $('#focusButton').addEventListener('click', () => {
    if (focusTimer) { stopFocus(false); return; }
    focusSeconds = 25 * 60;
    root.classList.add('focus-active');
    $('#focusButton').setAttribute('aria-pressed', 'true');
    $('#focusText').textContent = 'End focus session';
    $('#focusTimer').hidden = false;
    choosePose();
    updateFocusTimer();
    focusTimer = setInterval(() => {
      focusSeconds -= 1;
      updateFocusTimer();
      if (focusSeconds <= 0) stopFocus(true);
    }, 1000);
    poseTimer = setInterval(() => { if (!root.classList.contains('no-motion')) choosePose(); }, 20000);
  });

  const naviTrigger = $('#naviTrigger');
  const naviMenu = $('#naviMenu');
  const setNaviOpen = (open) => {
    naviMenu.hidden = !open;
    naviTrigger.setAttribute('aria-expanded', String(open));
    naviTrigger.setAttribute('aria-label', open ? 'Close NAVI product shortcuts' : 'Open NAVI product shortcuts');
  };
  naviTrigger.addEventListener('click', () => setNaviOpen(naviMenu.hidden));

  $('#scaleSlider').style.setProperty('--ui-scale', String(uiScale));
  const closeOverlays = () => {
    setControlsOpen(false);
    setNaviOpen(false);
    themePanel.hidden = true;
  };
  document.addEventListener('click', (event) => {
    if (!event.target.closest('#controlsWrap')) setControlsOpen(false);
    if (!event.target.closest('#naviLauncher')) setNaviOpen(false);
    if (!event.target.closest('#controlsWrap') && !event.target.closest('#themePanel')) themePanel.hidden = true;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeOverlays();
    if (event.target.matches('input,textarea,[contenteditable=true]')) return;
    if (event.key.toLowerCase() === 'h') setHeaderVisible(header.classList.contains('header-hidden'), true);
    if (event.key.toLowerCase() === 'a') agentPanel.hidden = !agentPanel.hidden;
    if (event.key.toLowerCase() === 't') themePanel.hidden = !themePanel.hidden;
    if (event.key.toLowerCase() === 'c') setControlsOpen(controlsMenu.hidden);
  });

  const starCanvas = $('#stars');
  const starCtx = starCanvas.getContext('2d', { alpha: true });
  let stars = [];
  let starWidth = 0, starHeight = 0, starDpr = 1, starLast = 0, starFrame;
  const resizeStars = () => {
    starDpr = Math.min(window.devicePixelRatio || 1, 2);
    starWidth = window.innerWidth; starHeight = window.innerHeight;
    starCanvas.width = Math.round(starWidth * starDpr); starCanvas.height = Math.round(starHeight * starDpr);
    starCtx.setTransform(starDpr, 0, 0, starDpr, 0, 0);
    stars = Array.from({ length: Math.max(70, Math.min(190, Math.round(starWidth * starHeight / 9500))) }, () => ({
      x: Math.random() * starWidth, y: Math.random() * starHeight * .61,
      r: Math.random() * 1.15 + .3, a: Math.random() * .56 + .24,
      phase: Math.random() * Math.PI * 2, speed: Math.random() * .9 + .25
    }));
  };
  const drawStars = (now) => {
    starFrame = requestAnimationFrame(drawStars);
    if (document.hidden) { starLast = now; return; }
    const frozen = root.classList.contains('no-motion');
    const dt = frozen ? 0 : Math.min((now - starLast) / 1000 || 0, .06);
    starLast = now;
    starCtx.clearRect(0, 0, starWidth, starHeight);
    for (const star of stars) {
      if (!frozen) star.phase += dt * star.speed;
      const alpha = star.a * (.72 + .28 * Math.sin(star.phase));
      starCtx.globalAlpha = alpha;
      starCtx.fillStyle = '#d9fff0';
      starCtx.shadowColor = '#9dffd6';
      starCtx.shadowBlur = star.r > 1 ? 6 : 2;
      starCtx.beginPath(); starCtx.arc(star.x, star.y, star.r, 0, Math.PI * 2); starCtx.fill();
    }
    starCtx.globalAlpha = 1; starCtx.shadowBlur = 0;
  };

  const rainCanvas = $('#rain');
  const rainCtx = rainCanvas.getContext('2d', { alpha: true });
  let rainWidth = 0, rainHeight = 0, rainDpr = 1, rainColumns = [], rainLast = 0, rainFrame, rainDirty = true;
  const chars = 'ｦｱｳｴｵｶｷｸｹｻｼｽｿﾀﾂﾃﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾔﾕﾗﾘﾜ012345789ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  const randomGlyph = () => chars[Math.floor(Math.random() * chars.length)];
  const makeGlyphs = (count) => Array.from({ length: count }, () => randomGlyph());
  const resizeRain = () => {
    rainDpr = Math.min(window.devicePixelRatio || 1, 2);
    rainWidth = window.innerWidth; rainHeight = window.innerHeight;
    rainCanvas.width = Math.round(rainWidth * rainDpr); rainCanvas.height = Math.round(rainHeight * rainDpr);
    rainCtx.setTransform(rainDpr, 0, 0, rainDpr, 0, 0);
    const step = Math.max(28, Math.min(34, Math.round(rainWidth / 75)));
    const count = Math.ceil(rainWidth / step);
    rainColumns = Array.from({ length: count }, (_, i) => {
      const length = 8 + Math.floor(Math.random() * 20);
      return { x: i * step + step / 2, y: Math.random() * rainHeight, speed: 90 + Math.random() * 190, length, gap: step * 1.2, glyphs: makeGlyphs(length), mutate: Math.random() * 600 };
    });
    rainDirty = true;
  };
  const drawRain = (now) => {
    rainFrame = requestAnimationFrame(drawRain);
    const frozen = root.classList.contains('no-motion');
    if (document.hidden || (frozen && !rainDirty)) { rainLast = now; return; }
    const dt = frozen ? 0 : Math.min((now - rainLast) / 1000 || 0, .04);
    rainLast = now;
    rainCtx.clearRect(0, 0, rainWidth, rainHeight);
    rainCtx.textAlign = 'center'; rainCtx.textBaseline = 'middle';
    for (const column of rainColumns) {
      if (!frozen) { column.y += column.speed * dt; column.mutate -= dt * 1000; if (column.mutate <= 0) { column.glyphs[Math.floor(Math.random() * column.length)] = randomGlyph(); column.mutate = 100 + Math.random() * 360; } }
      rainCtx.font = 'italic 15px "Doto","MS Gothic",Consolas,monospace';
      for (let index = 0; index < column.length; index += 1) {
        const y = column.y - index * column.gap;
        if (y < -18 || y > rainHeight + 18) continue;
        rainCtx.globalAlpha = index === 0 ? .98 : Math.max(.08, .75 * (1 - index / column.length));
        rainCtx.fillStyle = index === 0 ? '#eafff3' : index === 1 ? '#8dffaf' : '#00e85b';
        rainCtx.shadowColor = index === 0 ? '#eafff3' : '#00ff66'; rainCtx.shadowBlur = index === 0 ? 18 : 7;
        rainCtx.fillText(column.glyphs[index], column.x, y);
      }
      if (!frozen && column.y - column.length * column.gap > rainHeight) {
        column.y = -Math.random() * rainHeight * .7; column.speed = 90 + Math.random() * 190;
      }
    }
    rainCtx.globalAlpha = 1; rainCtx.shadowBlur = 0; rainDirty = false;
  };
  resizeStars(); resizeRain();
  starFrame = requestAnimationFrame(drawStars);
  rainFrame = requestAnimationFrame(drawRain);
  window.addEventListener('resize', () => { resizeStars(); resizeRain(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { starLast = rainLast = performance.now(); } });
  window.addEventListener('pagehide', () => { cancelAnimationFrame(starFrame); cancelAnimationFrame(rainFrame); clearInterval(messageTimer); clearInterval(focusTimer); clearInterval(poseTimer); });
})();
