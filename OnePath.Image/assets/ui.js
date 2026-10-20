(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const root = document.documentElement;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
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

  const wallpapers = {
    original: { label: 'Original', src: 'assets/matrix-wallpaper-starless.png', overlay: false },
    desert: { label: 'Dune sea', src: 'wallpaper-review-v5/01-dune-sea.png', overlay: true },
    city: { label: 'Distant city', src: 'wallpaper-review-v5/02-distant-city.png', overlay: true },
    temple: { label: 'Sunken sanctuary', src: 'wallpaper-review-v5/03-sunken-sanctuary.png', overlay: true },
    ice: { label: 'Glacial inlet', src: 'wallpaper-review-v5/04-glacial-inlet.png', overlay: true },
    orbital: { label: 'Cloudport', src: 'wallpaper-review-v5/05-cloudport.png', overlay: true }
  };
  let selectedWallpaper = Object.hasOwn(wallpapers, saved.wallpaper) ? saved.wallpaper : 'original';

  let selectedAgent = saved.agent === null ? null : agents[saved.agent] ? saved.agent : 'Gandalf';
  let messageMode = 'greeting';
  let quoteIndex = 0;
  let use24Hour = saved.clock24 !== false;
  let executionMode = saved.executionMode === 'fusion' ? 'fusion' : 'local';
  let uiScale = Math.max(.8, Math.min(1.2, Number(saved.scale) || 1));
  root.style.setProperty('--ui-scale', String(uiScale));
  let focusTimer = null;
  let focusSeconds = 25 * 60;
  let focusHoldTimer = null;
  let autoFocusIdleTimer = null;
  let autoFocusEnabled = false;
  let focusStartedAutomatically = false;
  let focusKeyHoldTriggered = false;
  let focusKeyPressedDuringAutoSession = false;
  let poseTimer = null;
  let messageTimer = null;
  let currentPose = '';
  let toastTimer = null;

  let preferencesReady = false;
  const persist = () => {
    if (!preferencesReady) return;
    const data = {
      theme: root.dataset.theme || 'mint',
      motion: !root.classList.contains('no-motion'),
      crt: root.classList.contains('crt-enabled'),
      scale: uiScale,
      agent: selectedAgent,
      executionMode,
      wallpaper: selectedWallpaper,
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
  const constellationAsset = (agent) => {
    const versions = { Hermes: 'hermes-imagegen-v2.png', Atomic: 'atomic-imagegen-v3.png' };
    return `assets/constellation-references/${versions[agent] || `${agent.toLowerCase()}-imagegen.png`}`;
  };
  const constellationFor = (agent, image = document.createElement('img')) => {
    image.className = 'constellation-artwork';
    image.alt = '';
    image.decoding = 'async';
    if (!image.getAttribute('src')) image.src = constellationAsset(agent);
    const aurora = document.createElement('div');
    aurora.className = 'constellation-aurora';
    aurora.style.setProperty('--constellation-mask', `url("${image.src}")`);
    motif.dataset.agent = agent;
    motif.replaceChildren(image, aurora);
    motif.dataset.artwork = image.getAttribute('src');
    motif.setAttribute('aria-label', `${agent} aurora constellation across the upper sky behind the clouds`);
  };

  const constellationExitTime = () => reducedMotion.matches || root.classList.contains('no-motion') ? 350 : 1600;
  const switchConstellation = async (name) => {
    const request = ++motifRequest;
    const image = new Image();
    image.src = constellationAsset(name);
    const ready = image.decode().catch(() => null);
    motif.dataset.pendingAgent = name;
    motif.classList.add('changing');
    await Promise.all([ready, new Promise(resolve => setTimeout(resolve, motif.childElementCount ? constellationExitTime() : 450))]);
    if (request !== motifRequest) return;
    if (!image.naturalWidth) {
      motif.classList.remove('changing');
      delete motif.dataset.pendingAgent;
      toast(`${name} sky artwork could not load`);
      return;
    }
    constellationFor(name, image);
    // Let the new artwork paint at zero opacity before its long reveal.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (request !== motifRequest) return;
      motif.classList.remove('changing');
      delete motif.dataset.pendingAgent;
    }));
  };

  const chatDrafts = new Map();
  let chatDraftAgent = selectedAgent;
  const updateChatAgent = () => {
    chatDrafts.set(chatDraftAgent, $('#chatInput').value);
    chatDraftAgent = selectedAgent;
    const active = Boolean(selectedAgent);
    $('#agentChat').disabled = !active;
    $('#chatSubmit').disabled = !active;
    $('#chatInput').disabled = !active;
    if (!active) {
      $('#chatInput').value = '';
      $('#chatInput').placeholder = 'Choose an agent to begin';
      $('#chatHeading').textContent = 'NO MODEL SELECTED';
      $('#chatLabel').textContent = 'Choose an agent to chat';
      $('#agentChat').setAttribute('aria-label', 'Chat unavailable · choose an agent');
      $('#agentChat').setAttribute('aria-expanded', 'false');
      setDisclosure($('#chatPanel'), false);
      $('#chatStatus').textContent = 'Model offloaded in preview · runtime not connected';
      $('#chatFeedback').textContent = '';
      return;
    }
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
    if (open && !selectedAgent) return;
    setDisclosure($('#chatPanel'), open);
    $('#agentChat').setAttribute('aria-expanded', String(open));
    if(open) $('#chatInput').focus({preventScroll:true});
  };
  $('#agentChat').addEventListener('click', () => setChatOpen($('#agentChat').getAttribute('aria-expanded') !== 'true'));
  $('#chatForm').addEventListener('submit', event => {
    event.preventDefault();
    if (!selectedAgent || !$('#chatInput').value.trim()) return;
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

  const updateOffload = () => {
    if (!selectedAgent) $$('.agent').forEach(button => { button.classList.remove('active'); button.setAttribute('aria-pressed','false'); });
    $('#offloadModel').disabled = !selectedAgent;
    $('#offloadModel').setAttribute('aria-label', selectedAgent ? `Offload ${selectedAgent} model` : 'No model selected');
    $('#offloadStatus').textContent = selectedAgent ? `${selectedAgent} · preview` : 'No model selected';
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
    switchConstellation(name);
    updateChatAgent();
    updateOffload();
    persist();
  };
  $$('.agent').forEach((button) => button.addEventListener('click', () => selectAgent(button.dataset.agent)));
  $('#offloadModel').addEventListener('click', () => {
    if (!selectedAgent) return;
    const previous = selectedAgent;
    selectedAgent = null;
    $$('.agent').forEach(button => { button.classList.remove('active'); button.setAttribute('aria-pressed','false'); });
    messageMode = 'greeting';
    paintMessage(true);
    const request = ++motifRequest;
    motif.classList.add('changing');
    setTimeout(() => {
      if (request !== motifRequest) return;
      motif.replaceChildren();
      delete motif.dataset.agent;
      delete motif.dataset.artwork;
      delete motif.dataset.pendingAgent;
      motif.setAttribute('aria-label','No model selected');
      motif.classList.remove('changing');
    }, constellationExitTime());
    updateChatAgent();
    updateOffload();
    persist();
    toast(`${previous} offloaded in preview · runtime not connected`);
  });
  if (selectedAgent) selectAgent(selectedAgent);
  else { updateOffload(); updateChatAgent(); paintMessage(false); }

  messageTimer = setInterval(() => {
    if (!selectedAgent || root.classList.contains('no-motion') || document.hidden) return;
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
  preferencesReady = true;

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
    if (reducedMotion.matches || root.classList.contains('no-motion')) return;
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
    if (document.hidden || reducedMotion.matches || root.classList.contains('no-motion')) return;
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
    focusStartedAutomatically = false;
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
  const startFocus = (automatic = false) => {
    if (focusTimer) return;
    focusSeconds = 25 * 60;
    root.classList.add('focus-active');
    focusStartedAutomatically = automatic;
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
  };
  const scheduleAutoFocus = () => {
    clearTimeout(autoFocusIdleTimer);
    if (!autoFocusEnabled || focusTimer) return;
    autoFocusIdleTimer = setTimeout(() => {
      autoFocusIdleTimer = null;
      startFocus(true);
    }, 60000);
  };
  const setAutoFocusEnabled = (enabled) => {
    autoFocusEnabled = enabled;
    root.classList.toggle('auto-focus-active', enabled);
    $('#focusButton').setAttribute('title', enabled ? 'Auto focus enabled · enters after 1 minute idle' : 'Focus');
    if (!enabled) {
      clearTimeout(autoFocusIdleTimer);
      autoFocusIdleTimer = null;
      if (focusStartedAutomatically) stopFocus(false);
      return;
    }
    scheduleAutoFocus();
  };
  const recordActivity = (event) => {
    if (event?.target instanceof Element && event.target.closest('#focusButton')) return;
    if (!autoFocusEnabled) return;
    if (focusStartedAutomatically) stopFocus(false);
    scheduleAutoFocus();
  };
  $('#focusButton').addEventListener('click', () => {
    if (focusTimer) { stopFocus(false); return; }
    startFocus();
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
    if (event.key.toLowerCase() === 'f' && !event.altKey && !event.ctrlKey && !event.metaKey) {
      if (event.repeat) return;
      clearTimeout(focusHoldTimer);
      focusKeyHoldTriggered = false;
      focusKeyPressedDuringAutoSession = focusStartedAutomatically;
      if (focusKeyPressedDuringAutoSession) { stopFocus(false); scheduleAutoFocus(); }
      focusHoldTimer = setTimeout(() => {
        focusHoldTimer = null;
        focusKeyHoldTriggered = true;
        setAutoFocusEnabled(!autoFocusEnabled);
      }, 3000);
      return;
    }
    recordActivity();
    if (event.key.toLowerCase() === 'h') setHeaderVisible(header.classList.contains('header-hidden'), true);
    if (event.key.toLowerCase() === 'a') setDisclosure(agentPanel, agentPanel.hidden || agentPanel.classList.contains('is-closed'));
    if (event.key.toLowerCase() === 't') setDisclosure(themePanel, themePanel.hidden || themePanel.classList.contains('is-closed'));
    if (event.key.toLowerCase() === 'c') setControlsOpen(controlsToggle.getAttribute('aria-expanded') !== 'true');
  });
  document.addEventListener('keyup', (event) => {
    if (event.key.toLowerCase() === 'f') {
      clearTimeout(focusHoldTimer); focusHoldTimer = null;
      if (!focusKeyHoldTriggered && !focusKeyPressedDuringAutoSession) $('#focusButton').click();
      else focusKeyHoldTriggered = false;
      focusKeyPressedDuringAutoSession = false;
      scheduleAutoFocus();
      return;
    }
    recordActivity();
  });
  window.addEventListener('blur', () => { clearTimeout(focusHoldTimer); focusHoldTimer = null; });
  window.addEventListener('mousemove', recordActivity, { passive: true });
  window.addEventListener('pointerdown', recordActivity, { passive: true });
  window.addEventListener('wheel', recordActivity, { passive: true });
  window.addEventListener('touchstart', recordActivity, { passive: true });

  const landscapeCanvas = $('#cloudLandscape');
  // Screen centre is neutral. Keep pointer depth separate from model-switch transforms.
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
  const parallax = { x: 0, y: 0, targetX: 0, targetY: 0, frame: 0, last: 0 };
  const parallaxEnabled = () => finePointer.matches && !reducedMotion.matches && !root.classList.contains('no-motion');
  const drawParallax = (now) => {
    const dt = Math.min((now - parallax.last) / 1000 || 1 / 60, .05);
    parallax.last = now;
    const ease = 1 - Math.exp(-dt / .32);
    parallax.x += (parallax.targetX - parallax.x) * ease;
    parallax.y += (parallax.targetY - parallax.y) * ease;
    const settled = Math.abs(parallax.targetX - parallax.x) < .0001 && Math.abs(parallax.targetY - parallax.y) < .0001;
    if (settled) { parallax.x = parallax.targetX; parallax.y = parallax.targetY; }
    $('#sceneEffects').style.setProperty('--parallax-x', parallax.x.toFixed(5));
    $('#sceneEffects').style.setProperty('--parallax-y', parallax.y.toFixed(5));
    parallax.frame = settled ? 0 : requestAnimationFrame(drawParallax);
  };
  const setParallaxTarget = (x = 0, y = 0) => {
    parallax.targetX = x; parallax.targetY = y;
    if (!parallax.frame && !document.hidden) {
      parallax.last = performance.now();
      parallax.frame = requestAnimationFrame(drawParallax);
    }
  };
  window.addEventListener('pointermove', (event) => {
    if (!parallaxEnabled() || event.pointerType === 'touch') return;
    const clamp = (value) => Math.max(-1, Math.min(1, value));
    setParallaxTarget(clamp(event.clientX / innerWidth * 2 - 1), clamp(event.clientY / innerHeight * 2 - 1));
  }, { passive: true });
  document.addEventListener('pointerleave', () => setParallaxTarget());
  window.addEventListener('blur', () => setParallaxTarget());
  window.addEventListener('resize', () => setParallaxTarget());
  reducedMotion.addEventListener('change', () => setParallaxTarget());
  finePointer.addEventListener('change', () => setParallaxTarget());
  const parallaxPreferences = new MutationObserver(() => { if (!parallaxEnabled()) setParallaxTarget(); });
  parallaxPreferences.observe(root, { attributes: true, attributeFilter: ['class'] });
  document.addEventListener('visibilitychange', () => {
    cancelAnimationFrame(parallax.frame); parallax.frame = 0;
    if (!document.hidden) setParallaxTarget();
  });
  window.addEventListener('pagehide', () => { cancelAnimationFrame(parallax.frame); parallaxPreferences.disconnect(); });
  const landscapeCtx = landscapeCanvas.getContext('2d', { willReadFrequently: true });
  let landscapeImage = null;
  let wallpaperRequest = 0;
  const wallpaperStatus = $('#wallpaperStatus');
  const wallpaperButtons = [];
  const smoothstep = (low, high, value) => { const t = Math.max(0,Math.min(1,(value-low)/(high-low))); return t*t*(3-2*t); };
  const renderLandscape = () => {
    if (!landscapeImage || !landscapeImage.naturalWidth) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const scale = Math.min(dpr, 4096 / Math.max(innerWidth, innerHeight));
    const width = Math.round(innerWidth * scale), height = Math.round(innerHeight * scale);
    landscapeCanvas.width = width; landscapeCanvas.height = height;
    const overlay = wallpapers[selectedWallpaper].overlay;
    // Bottom-anchor transparent foregrounds; preserve their mist and supplied alpha.
    const fit = Math.max(width / landscapeImage.naturalWidth, height / landscapeImage.naturalHeight);
    const iw = landscapeImage.naturalWidth * fit, ih = landscapeImage.naturalHeight * fit;
    landscapeCtx.drawImage(landscapeImage, (width-iw)/2, overlay ? height-ih : (height-ih)/2, iw, ih);
    if (overlay) return;
    // The original opaque wallpaper still needs its sky removed at runtime.
    const pixels = landscapeCtx.getImageData(0,0,width,height);
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
  const updateWallpaperButtons = () => {
    wallpaperButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.wallpaper === selectedWallpaper)));
  };
  const selectWallpaper = async (name, save = true) => {
    if (!Object.hasOwn(wallpapers, name)) return;
    const request = ++wallpaperRequest;
    wallpaperStatus.textContent = `Loading ${wallpapers[name].label}…`;
    $('#wallpaperChoices').setAttribute('aria-busy', 'true');
    const image = new Image();
    image.src = wallpapers[name].src;
    try {
      await image.decode();
      if (request !== wallpaperRequest) return;
      selectedWallpaper = name;
      landscapeImage = image;
      renderLandscape();
      $('#wallpaperFallback').hidden = true;
      root.dataset.wallpaper = name;
      root.style.setProperty('--wallpaper', `url("${new URL(wallpapers[name].src, document.baseURI).href}")`);
      updateWallpaperButtons();
      wallpaperStatus.textContent = `${wallpapers[name].label} selected`;
      if (save) persist();
    } catch {
      if (request !== wallpaperRequest) return;
      wallpaperStatus.textContent = `Could not load ${wallpapers[name].label}. Try again.`;
      // Keep the last working landscape and preference when a request fails.
      if (!landscapeImage) $('#wallpaperFallback').hidden = false;
    } finally {
      if (request === wallpaperRequest) $('#wallpaperChoices').setAttribute('aria-busy', 'false');
    }
  };
  Object.entries(wallpapers).forEach(([name, wallpaper]) => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'wallpaper-choice';
    button.dataset.wallpaper = name;
    button.setAttribute('aria-pressed', 'false');
    const thumbnail = document.createElement('img');
    thumbnail.src = wallpaper.src; thumbnail.alt = ''; thumbnail.loading = 'lazy';
    const label = document.createElement('span'); label.textContent = wallpaper.label;
    button.append(thumbnail, label);
    button.addEventListener('click', () => selectWallpaper(name));
    $('#wallpaperChoices').append(button); wallpaperButtons.push(button);
  });
  selectWallpaper(selectedWallpaper, false);
  let landscapeResizeTimer;
  window.addEventListener('resize',()=> { clearTimeout(landscapeResizeTimer); landscapeResizeTimer=setTimeout(renderLandscape,90); });

  const starCanvas = $('#stars');
  const starCtx = starCanvas.getContext('2d', { alpha: true });
  let stars = [];
  let starEventIn = .4 + Math.random() * 1.4;
  // Cache the soft halo once; animation changes each star's light, not its position.
  const starHalo = document.createElement('canvas');
  starHalo.width = starHalo.height = 64;
  const haloCtx = starHalo.getContext('2d');
  const haloLight = haloCtx.createRadialGradient(32,32,0,32,32,32);
  haloLight.addColorStop(0,'rgba(255,255,255,1)');
  haloLight.addColorStop(.08,'rgba(225,239,255,.95)');
  haloLight.addColorStop(.22,'rgba(164,201,255,.32)');
  haloLight.addColorStop(.5,'rgba(137,183,255,.09)');
  haloLight.addColorStop(1,'rgba(137,183,255,0)');
  haloCtx.fillStyle = haloLight; haloCtx.fillRect(0,0,64,64);
  let starWidth = 0, starHeight = 0, starDpr = 1, starLast = 0, starFrame;
  const resizeStars = () => {
    starDpr = Math.min(window.devicePixelRatio || 1, 2);
    starWidth = window.innerWidth; starHeight = window.innerHeight;
    starCanvas.width = Math.round(starWidth * starDpr); starCanvas.height = Math.round(starHeight * starDpr);
    starCtx.setTransform(starDpr, 0, 0, starDpr, 0, 0);
    stars = Array.from({ length: Math.max(95, Math.min(220, Math.round(starWidth * starHeight / 8500))) }, () => ({
      x: Math.random() * starWidth, y: Math.random() * starHeight * .61,
      r: Math.random() * .85 + .45, a: Math.random() * .3 + .5, bright: Math.random() < .08,
      flare: 0, flareDuration: 1, flareStrength: 1
    }));
  };
  const drawStars = (now) => {
    starFrame = requestAnimationFrame(drawStars);
    if (document.hidden) { starLast = now; return; }
    const frozen = root.classList.contains('no-motion') || reducedMotion.matches;
    const dt = frozen ? 0 : Math.min((now - starLast) / 1000 || 0, .06);
    starLast = now;
    // Independent random events: most stars stay steady, at most three glint.
    if (!frozen) {
      starEventIn -= dt;
      if (starEventIn <= 0) {
        const active = stars.filter(star => star.flare > 0).length;
        const choices = stars.filter(star => star.flare <= 0 && star.y < starHeight * .53);
        if (active < 3 && choices.length) {
          const chosen = choices[Math.floor(Math.random() * choices.length)];
          chosen.flareDuration = .85 + Math.random() * 1.65;
          chosen.flare = chosen.flareDuration;
          chosen.flareStrength = .8 + Math.random() * .7;
        }
        // Fresh, non-periodic interval and a fresh star after every event.
        starEventIn = .35 + Math.min(4.8, -Math.log(1 - Math.random()) * 1.35);
      }
    }
    starCtx.clearRect(0, 0, starWidth, starHeight);
    for (const star of stars) {
      if (!frozen) star.flare = Math.max(0, star.flare - dt);
      const progress = 1 - star.flare / star.flareDuration;
      const glint = star.flare > 0 ? Math.pow(Math.sin(Math.PI * progress), 2) * star.flareStrength : 0;
      const radius = star.r * (star.bright ? 1.4 : 1) * (1 + glint * .2);
      const haloSize = radius * (star.bright ? 16 : 10) * (1 + glint * .65);
      starCtx.globalAlpha = Math.min(1, star.a * (.86 + glint * .5));
      starCtx.drawImage(starHalo,star.x-haloSize/2,star.y-haloSize/2,haloSize,haloSize);
      starCtx.fillStyle = '#f6faff';
      starCtx.beginPath(); starCtx.arc(star.x,star.y,radius * .68,0,Math.PI*2); starCtx.fill();
      if (star.bright || glint > .025) {
        const reach = radius * (2.5 + glint * 7);
        starCtx.globalAlpha = Math.min(1, star.a * ((star.bright ? .1 : 0) + glint * .85));
        const rays = starCtx.createRadialGradient(star.x,star.y,0,star.x,star.y,reach);
        rays.addColorStop(0,'rgba(255,255,255,1)');
        rays.addColorStop(.3,'rgba(207,229,255,.75)');
        rays.addColorStop(1,'rgba(155,201,255,0)');
        starCtx.fillStyle = rays;
        starCtx.beginPath();
        starCtx.moveTo(star.x,star.y-reach); starCtx.lineTo(star.x+.7,star.y-.7);
        starCtx.lineTo(star.x+reach*.72,star.y); starCtx.lineTo(star.x+.7,star.y+.7);
        starCtx.lineTo(star.x,star.y+reach); starCtx.lineTo(star.x-.7,star.y+.7);
        starCtx.lineTo(star.x-reach*.72,star.y); starCtx.lineTo(star.x-.7,star.y-.7);
        starCtx.closePath(); starCtx.fill();
      }
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
      if (!frozen) { column.y += column.speed * dt * (root.classList.contains('focus-active') ? .2 : 1); column.mutate -= dt * 1000; if (column.mutate <= 0) { column.glyphs[Math.floor(Math.random() * column.length)] = randomGlyph(); column.mutate = (100 + Math.random() * 360) / 3; } }
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
