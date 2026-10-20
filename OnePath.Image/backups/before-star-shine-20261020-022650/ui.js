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
  let executionMode = saved.executionMode === 'fusion' ? 'fusion' : 'local';
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
      executionMode,
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

  const disclosureTimers = new WeakMap();
  const setDisclosure = (node, open) => {
    clearTimeout(disclosureTimers.get(node));
    if (open) {
      if (!node.hidden && !node.classList.contains('is-closed')) return;
      node.classList.add('is-closed');
      node.hidden = false;
      requestAnimationFrame(() => requestAnimationFrame(() => node.classList.remove('is-closed')));
    } else {
      node.classList.add('is-closed');
      disclosureTimers.set(node, setTimeout(() => { node.hidden = true; }, 330));
    }
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
  const constellationFor = (agent) => {
    const image = document.createElement('img');
    image.className = 'constellation-artwork';
    image.alt = '';
    image.decoding = 'async';
    image.src = `assets/constellation-references/${agent.toLowerCase()}-imagegen.png`;
    motif.replaceChildren(image);
    motif.dataset.artwork = image.getAttribute('src');
    motif.setAttribute('aria-label', `${agent} ImageGen constellation artwork across the upper sky behind the clouds`);
  };

  const chatDrafts = new Map();
  let chatDraftAgent = selectedAgent;
  const updateChatAgent = () => {
    chatDrafts.set(chatDraftAgent, $('#chatInput').value);
    chatDraftAgent = selectedAgent;
    $('#chatInput').value = chatDrafts.get(selectedAgent) || '';
    $('#chatInput').placeholder = `Ask ${selectedAgent}…`;
    $('#chatHeading').textContent = `CHAT WITH ${selectedAgent.toUpperCase()}`;
    $('#chatLabel').textContent = `Chat with ${selectedAgent}`;
    $('#agentChat').setAttribute('aria-label', `Chat with ${selectedAgent}`);
    $('#chatPanel').setAttribute('aria-label', `Chat with ${selectedAgent}`);
    $('#chatStatus').textContent = selectedAgent === 'Gandalf' ? 'Cloud only · connect Gandalf to receive replies' : `${executionMode === 'fusion' ? 'Local + cloud' : 'Local'} preview · connect an agent to receive replies`;
    $('#chatFeedback').textContent = '';
  };
  const setChatOpen = (open) => {
    setDisclosure($('#chatPanel'), open);
    $('#agentChat').setAttribute('aria-expanded', String(open));
    if(open) $('#chatInput').focus({preventScroll:true});
  };
  $('#agentChat').addEventListener('click', () => setChatOpen($('#agentChat').getAttribute('aria-expanded') !== 'true'));
  $('#chatForm').addEventListener('submit', event => {
    event.preventDefault();
    if (!$('#chatInput').value.trim()) return;
    chatDrafts.set(selectedAgent, $('#chatInput').value);
    $('#chatFeedback').textContent = `Draft kept for ${selectedAgent} in this tab. Agent connection is not configured.`;
  });
  const setExecutionMode = (mode, save = true) => {
    executionMode = mode === 'fusion' ? 'fusion' : 'local';
    root.dataset.executionMode = executionMode;
    $('#modeLabel').textContent = executionMode.toUpperCase();
    $('#modeToggle').setAttribute('aria-label', `Execution mode: ${executionMode === 'fusion' ? 'Fusion, local plus cloud' : 'Local'}`);
    $$('[data-mode]').forEach(button => button.setAttribute('aria-pressed',String(button.dataset.mode === executionMode)));
    updateChatAgent();
    if(save) persist();
  };
  const setModeOpen = (open) => { setDisclosure($('#modeMenu'), open); $('#modeToggle').setAttribute('aria-expanded', String(open)); };
  $('#modeToggle').addEventListener('click',()=>setModeOpen($('#modeToggle').getAttribute('aria-expanded') !== 'true'));
  $$('[data-mode]').forEach(button=>button.addEventListener('click',()=>{setExecutionMode(button.dataset.mode);setModeOpen(false);}));
  setExecutionMode(executionMode, false);

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
    updateChatAgent();
    persist();
  };
  $$('.agent').forEach((button) => button.addEventListener('click', () => selectAgent(button.dataset.agent)));
  selectAgent(selectedAgent);

  messageTimer = setInterval(() => {
    if (root.classList.contains('no-motion') || document.hidden) return;
    if (messageMode === 'greeting') { messageMode = 'agent'; }
    else { messageMode = 'greeting'; quoteIndex = (quoteIndex + 1) % agents[selectedAgent].quotes.length; }
    paintMessage(true);
  }, 300000);


  const controlsToggle = $('#controlsToggle');
  const controlsMenu = $('#controlsMenu');
  const setControlsOpen = (open) => {
    setDisclosure(controlsMenu, open);
    controlsToggle.setAttribute('aria-expanded', String(open));
    controlsToggle.setAttribute('aria-label', open ? 'Close controls' : 'Open controls');
    controlsToggle.title = open ? 'Close controls' : 'Controls';
  };
  controlsToggle.addEventListener('click', () => setControlsOpen(controlsToggle.getAttribute('aria-expanded') !== 'true'));

  const setPanel = (panel, open) => setDisclosure(panel, open);
  $$('.menu-item').forEach((button) => button.addEventListener('click', () => {
    const action = button.dataset.action;
    if (action === 'agents') {
      const isVisible = !agentPanel.hidden && !agentPanel.classList.contains('is-closed');
      setDisclosure(agentPanel, !isVisible);
      toast(isVisible ? 'Agent picker hidden · open Controls to restore' : 'Agent picker shown');
    } else if (action === 'appearance') {
      setPanel($('#themePanel'), $('#themePanel').hidden || $('#themePanel').classList.contains('is-closed'));
    } else if (action === 'name') {
      setPanel($('#namePanel'), $('#namePanel').hidden || $('#namePanel').classList.contains('is-closed'));
    } else {
      toast(action === 'command' ? 'Command is a preview · not connected' : 'Files are a preview · not connected');
    }
  }));
  $$('.panel-close').forEach((button) => button.addEventListener('click', () => { if(button.dataset.close === 'chatPanel') {setChatOpen(false);$('#agentChat').focus();} else setPanel($(`#${button.dataset.close}`), false); }));

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

  // True character frames follow the four-legged ImageGen cat reference.
  const asciiFrames = window.ONEPATH_NAVI_FRAMES;
  let naviWalking = false, naviRoamX = 0, naviRoamTime = 0, naviDirection = 1;
  let asciiFrame = 0;
  const drawNavi = () => {
    const frames = asciiFrames[currentPose] || (naviWalking ? asciiFrames.walking : asciiFrames.idle);
    $('#naviAscii').textContent = frames[asciiFrame % frames.length];
    const trigger = $('#naviTrigger');
    trigger.dataset.facing = naviDirection > 0 ? 'right' : 'left';
    if (trigger.getAttribute('aria-expanded') !== 'true') trigger.setAttribute('aria-label', `NAVI ${currentPose || 'ASCII cat'} · open product shortcuts`);
  };
  const choosePose = () => {
    const options = ['working','coffee','sleeping','meditating'].filter(pose => pose !== currentPose);
    currentPose = options[Math.floor(Math.random() * options.length)];
    asciiElapsed = 0; asciiFrame = 0;
    $('#naviTrigger').dataset.pose = currentPose;
    drawNavi();
  };
  const asciiDurations = {
    idle: [1600,280,130,180,1100,420], walking: [110,110,110,110,110,110,110,110],
    working: [140,130,140,130], coffee: [800,500,400,900],
    sleeping: [1000,1000,1000,1000], meditating: [700,700,700,700]
  };
  let asciiElapsed = 0, asciiLast = 0, asciiFrameRequest;
  drawNavi();
  const animateAscii = (now) => {
    asciiFrameRequest = requestAnimationFrame(animateAscii);
    const dt = Math.min(now - (asciiLast || now), 100);
    asciiLast = now;
    if (document.hidden || root.classList.contains('no-motion')) return;
    const launcher = $('#naviLauncher');
    const held = launcher.matches(':hover,:focus-within') || $('#naviTrigger').getAttribute('aria-expanded') === 'true';
    if (!currentPose && !held) {
      naviRoamTime += dt;
      const walking = naviRoamTime % 17000 > 4500;
      if (walking !== naviWalking) { naviWalking = walking; asciiFrame = 0; asciiElapsed = 0; drawNavi(); }
      if (naviWalking) {
        const maxX = Math.max(0, Math.min(230, innerWidth / uiScale * .28 - 100));
        naviRoamX = Math.max(0, Math.min(maxX, naviRoamX + naviDirection * dt * .016));
        if (naviRoamX >= maxX) naviDirection = -1;
        if (naviRoamX <= 0) naviDirection = 1;
        launcher.style.setProperty('--roam-x', `${naviRoamX.toFixed(2)}px`);
      }
    } else if (naviWalking) { naviWalking = false; asciiFrame = 0; drawNavi(); }
    const pose = asciiFrames[currentPose] ? currentPose : naviWalking ? 'walking' : 'idle';
    const frames = asciiFrames[pose];
    asciiElapsed += dt;
    const duration = asciiDurations[pose][asciiFrame % frames.length];
    if (asciiElapsed >= duration) {
      asciiElapsed %= duration;
      asciiFrame = (asciiFrame + 1) % frames.length;
      drawNavi();
    }
  };
  asciiFrameRequest = requestAnimationFrame(animateAscii);
  const stopFocus = (completed = false) => {
    clearInterval(focusTimer);
    clearInterval(poseTimer);
    focusTimer = poseTimer = null;
    root.classList.remove('focus-active');
    $('#focusButton').setAttribute('aria-pressed', 'false');
    $('#focusText').textContent = 'Focus';
    $('#focusButton').setAttribute('aria-label', 'Start focus session');
    currentPose = ''; asciiFrame = 0; drawNavi();
    delete $('#naviTrigger').dataset.pose;
    $('#focusTimer').hidden = true;
    focusSeconds = 25 * 60;
    if (completed) toast('Focus session complete');
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
    $('#focusText').textContent = 'FOCUSING';
    $('#focusButton').setAttribute('aria-label', 'End focus session');
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
    setDisclosure(naviMenu, open);
    naviTrigger.setAttribute('aria-expanded', String(open));
    naviTrigger.setAttribute('aria-label', open ? 'Close NAVI product shortcuts' : 'Open NAVI product shortcuts');
  };
  naviTrigger.addEventListener('click', () => setNaviOpen(naviTrigger.getAttribute('aria-expanded') !== 'true'));

  $('#scaleSlider').style.setProperty('--ui-scale', String(uiScale));
  const closeOverlays = () => {
    setControlsOpen(false);
    setNaviOpen(false);
    setModeOpen(false); setChatOpen(false);
    setDisclosure(themePanel, false); setDisclosure($('#namePanel'), false);
  };
  document.addEventListener('click', (event) => {
    if (!event.target.closest('#modeWrap')) setModeOpen(false);
    if (!event.target.closest('#controlsWrap')) setControlsOpen(false);
    if (!event.target.closest('#naviLauncher')) setNaviOpen(false);
    if (!event.target.closest('#controlsWrap') && !event.target.closest('#themePanel')) {
      setDisclosure(themePanel, false);
      setDisclosure($('#namePanel'), false);
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeOverlays();
    if (event.target.matches('input,textarea,[contenteditable=true]')) return;
    if (event.key.toLowerCase() === 'h') setHeaderVisible(header.classList.contains('header-hidden'), true);
    if (event.key.toLowerCase() === 'a') setDisclosure(agentPanel, agentPanel.hidden || agentPanel.classList.contains('is-closed'));
    if (event.key.toLowerCase() === 't') setDisclosure(themePanel, themePanel.hidden || themePanel.classList.contains('is-closed'));
    if (event.key.toLowerCase() === 'c') setControlsOpen(controlsToggle.getAttribute('aria-expanded') !== 'true');
  });

  const landscapeCanvas = $('#cloudLandscape');
  const landscapeCtx = landscapeCanvas.getContext('2d', { willReadFrequently: true });
  const landscapeImage = new Image();
  const smoothstep = (low, high, value) => { const t = Math.max(0,Math.min(1,(value-low)/(high-low))); return t*t*(3-2*t); };
  const renderLandscape = () => {
    if (!landscapeImage.complete || !landscapeImage.naturalWidth) return;
    const scale = Math.min(1, 2048 / Math.max(innerWidth,innerHeight));
    const width = Math.round(innerWidth*scale), height = Math.round(innerHeight*scale);
    landscapeCanvas.width=width; landscapeCanvas.height=height;
    const fit = Math.max(width/landscapeImage.naturalWidth,height/landscapeImage.naturalHeight);
    const iw=landscapeImage.naturalWidth*fit, ih=landscapeImage.naturalHeight*fit;
    landscapeCtx.drawImage(landscapeImage,(width-iw)/2,(height-ih)/2,iw,ih);
    const pixels=landscapeCtx.getImageData(0,0,width,height);
    for (let y=0;y<height;y+=1) {
      const horizon=smoothstep(.29,.62,y/height);
      for(let x=0;x<width;x+=1) {
        const i=(y*width+x)*4;
        const luminance=pixels.data[i]*.22+pixels.data[i+1]*.65+pixels.data[i+2]*.13;
        const cloud=smoothstep(5,32,luminance);
        pixels.data[i+3]=Math.round(255*Math.max(horizon,cloud));
      }
    }
    landscapeCtx.putImageData(pixels,0,0);
  };
  landscapeImage.addEventListener('load',renderLandscape);
  landscapeImage.addEventListener('error',()=>{ $('#wallpaperFallback').hidden=false; });
  landscapeImage.src='assets/matrix-wallpaper-clean.png';
  let landscapeResizeTimer;
  window.addEventListener('resize',()=> { clearTimeout(landscapeResizeTimer); landscapeResizeTimer=setTimeout(renderLandscape,90); });

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
      return { x: i * step + step / 2, y: Math.random() * rainHeight, speed: 90 + Math.random() * 190, length, gap: step * 1.2, glyphs: makeGlyphs(length), mutate: Math.random() * 200 };
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
      if (!frozen) { column.y += column.speed * dt; column.mutate -= dt * 1000; if (column.mutate <= 0) { column.glyphs[Math.floor(Math.random() * column.length)] = randomGlyph(); column.mutate = (100 + Math.random() * 360) / 3; } }
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
  window.addEventListener('pagehide', () => { cancelAnimationFrame(starFrame); cancelAnimationFrame(rainFrame); clearInterval(messageTimer); clearInterval(focusTimer); clearInterval(poseTimer); cancelAnimationFrame(asciiFrameRequest); clearTimeout(landscapeResizeTimer); });
})();
