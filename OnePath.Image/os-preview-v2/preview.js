(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const root = document.documentElement;
  const frame = $('#homeScene');
  const menu = $('#spaceMenu');
  const dialog = $('#detail');
  let frameDocument, returnFocus, noticeTimer;
  let memoryDraft = 'Simple and intuitive, with advanced controls when needed. Keep the interface borderless, quiet and spacious. Use C for space navigation.';
  const departments = {
    engineering: { name: 'Engineering', purpose: 'Builds and integrates the work Gandalf assigns.', skill: 'Frontend implementation', memory: 'Project conventions', boundary: 'Reports blockers to Gandalf. Does not expand the mission or delegate further.' },
    uiux: { name: 'UI-UX', purpose: 'Reviews clarity, interaction, visual consistency and accessibility.', skill: 'Interface review', memory: 'Owner design preferences', boundary: 'Advises Gandalf on design decisions. Reviews within the assigned scope.' },
    research: { name: 'Research', purpose: 'Finds reliable sources and answers a bounded question.', skill: 'Source analysis', memory: 'Verified sources', boundary: 'Returns evidence and uncertainty to Gandalf before implementation decisions.' },
    verification: { name: 'Verification', purpose: 'Independently checks the completed work against the original acceptance criteria.', skill: 'Browser acceptance review', memory: 'Verification procedures', boundary: 'A fresh Verification-Worker checks at the end. It reports failures and never fixes its own findings.' }
  };
  const notify = (message) => {
    $('#notice').textContent = message;
    $('#notice').classList.add('show');
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => $('#notice').classList.remove('show'), 3400);
  };
  const updateClock = () => {
    const now = new Date();
    $('#hours').textContent = String(now.getHours()).padStart(2, '0');
    $('#minutes').textContent = String(now.getMinutes()).padStart(2, '0');
  };
  updateClock();
  setInterval(updateClock, 1000);

  const closeMenu = () => { menu.hidden = true; $('#cTrigger').setAttribute('aria-expanded', 'false'); };
  const syncFrame = () => {
    if (!frameDocument) return;
    frameDocument.documentElement.classList.toggle('preview-away', root.dataset.space !== 'home');
    frameDocument.documentElement.classList.toggle('preview-detail-open', !dialog.hidden);
    frameDocument.querySelector('.center').inert = root.dataset.space !== 'home';
  };
  const closeDetail = () => {
    if (dialog.hidden) return;
    dialog.hidden = true;
    root.classList.remove('detail-open');
    $('#spaces').inert = false;
    $('.space-control').inert = false;
    frame.inert = false;
    syncFrame();
    if (returnFocus?.isConnected) returnFocus.focus();
  };
  const setSpace = (name) => {
    if (!['home', 'workspace', 'company'].includes(name)) name = 'home';
    closeDetail();
    root.dataset.space = name;
    for (const space of document.querySelectorAll('.space')) space.hidden = space.id !== name;
    for (const button of document.querySelectorAll('[data-space]')) {
      if (button instanceof HTMLButtonElement) {
        if (button.dataset.space === name) button.setAttribute('aria-current', 'page');
        else button.removeAttribute('aria-current');
      }
    }
    history.replaceState(null, '', `#${name}`);
    closeMenu();
    syncFrame();
    $('#cTrigger').focus({preventScroll:true});
  };
  const openDetail = (context, title, markup) => {
    if (dialog.hidden) returnFocus = document.activeElement;
    $('#detailContext').textContent = context;
    $('#detailTitle').textContent = title;
    $('#detailBody').innerHTML = markup;
    dialog.hidden = false;
    dialog.scrollTop = 0;
    root.classList.add('detail-open');
    $('#spaces').inert = true;
    $('.space-control').inert = true;
    frame.inert = true;
    closeMenu();
    syncFrame();
    $('#closeDetail').focus({preventScroll:true});
  };
  const showPlan = () => openDetail('WORKSPACE / CURRENT TASK', 'A clear path forward.', `
    <p class="lede">Reduce idle graphics work while preserving the ambient experience.</p>
    <ol><li>Confirm the behaviour<small>Engineering · trace frozen stars and reduced-motion rain.</small></li><li>Make the smallest correction<small>Engineering · preserve artwork, agent transitions and focus.</small></li><li>Verify the result independently<small>Fresh Verification-Worker · motion settings, agent switching and typing.</small></li></ol>
    <h3>What counts as done</h3><p>Static effects stop repainting. Reduced motion is respected. Existing scenery and controls still work.</p>
    <details><summary>Scope, resources and recovery</summary><p>Two bounded Engineering steps, followed by one independent review. No backend work or artwork replacement. Keep changes reversible in Git. Any blocker returns to Gandalf.</p></details>
    <button class="primary" id="approvePlan">Approve plan ↗</button><p class="detail-note">Sample plan. This preview does not start agents or consume model credits.</p>`);
  const showDepartment = (id) => {
    const department = departments[id];
    if (!department) return;
    openDetail('COMPANY / DEPARTMENT', department.name, `
      <p class="lede">${department.purpose}</p><dl><dt>Reports to</dt><dd>Gandalf · Orchestrator</dd><dt>Availability</dt><dd>${id === 'verification' ? 'Reserved for independent final review' : 'Ready for a bounded assignment'}</dd><dt>Model policy</dt><dd>gpt-6-luna · high reasoning</dd><dt>Working boundary</dt><dd>${department.boundary}</dd></dl>
      <h3>Knowledge that stays</h3><p>${department.skill}<br>${department.memory}<br>Role instructions and reusable procedures</p><button class="primary" data-resource="skills">Inspect skills ↗</button><p class="detail-note">Illustrative department. Runtime connection pending.</p>`);
  };
  const resourceTabs = (selected) => `<div class="resource-tabs" aria-label="Resource type">${['models','skills','memory'].map(key => `<button data-resource="${key}" aria-pressed="${key === selected}">${key[0].toUpperCase() + key.slice(1)}</button>`).join('')}</div>`;
  const showResource = (kind) => {
    let markup = resourceTabs(kind);
    if (kind === 'models') markup += `<p class="lede">The right model, behind a stable role.</p><div class="resource-entry"><strong>Gandalf · gpt-6.1-sol</strong><small>Orchestrator · cloud only<br>Owner policy locks the lead model.</small></div><div class="resource-entry"><strong>Departments · gpt-6-luna</strong><small>High reasoning · bounded assignments<br>One shared default for Engineering, UI-UX, Research and Verification.</small></div><details><summary>Escalation policy</summary><p>Departments report difficulties to Gandalf. Stronger worker models require owner approval. Problem-Solver is a bounded diagnosis role using gpt-6.1-sol, medium reasoning.</p></details><p class="detail-note">Policy preview. No model runtime is connected.</p>`;
    if (kind === 'skills') markup += `<label class="sr-only" for="resourceSearch">Find a skill</label><input class="resource-search" id="resourceSearch" type="search" placeholder="Find a skill…"><div id="resourceEntries"><div class="resource-entry"><strong>Browser acceptance review</strong><small>Verification · reproduce behaviour and report evidence.</small><button data-inspect="verification">Inspect assignment ↗</button></div><div class="resource-entry"><strong>Interface review</strong><small>UI-UX · check clarity, hierarchy and accessibility.</small><button data-inspect="uiux">Inspect assignment ↗</button></div><div class="resource-entry"><strong>Source analysis</strong><small>Research · find primary sources and mark uncertainty.</small><button data-inspect="research">Inspect assignment ↗</button></div></div><p class="detail-note">Sample capabilities. Assignments are illustrative.</p>`;
    if (kind === 'memory') markup += `<p class="lede">Useful context, kept in the right place.</p><label class="sr-only" for="resourceSearch">Search memory</label><input class="resource-search" id="resourceSearch" type="search" placeholder="Search company and project memory…"><div id="resourceEntries"><div class="resource-entry"><strong>Owner design preferences</strong><small>Company · quiet, borderless and generous with empty space.</small><button id="editMemory">Read / edit ↗</button></div><div class="resource-entry"><strong>OnePath source of truth</strong><small>Project · decisions and implementation belong in the repository.</small></div><div class="resource-entry"><strong>Department continuity</strong><small>Department · instructions, skills and verified lessons survive model changes.</small></div></div><p class="detail-note">Sample entries. No external memory store is connected.</p>`;
    openDetail('COMPANY / RESOURCES', kind[0].toUpperCase() + kind.slice(1), markup);
  };
  const showHistory = () => openDetail('WORKSPACE / PAST TASKS', 'Pick up where you left off.', `<div class="resource-entry"><strong>Ambient performance</strong><small>Current · plan ready for review</small><button data-detail="plan">Open plan ↗</button></div><div class="resource-entry"><strong>Wallpaper atmosphere</strong><small>Sample completed task · source, changes and review together.</small><button id="inspectResult">Inspect result ↗</button></div><p class="detail-note">Illustrative history for this preview.</p>`);

  $('#cTrigger').addEventListener('click', () => {
    const open = menu.hidden;
    menu.hidden = !open;
    $('#cTrigger').setAttribute('aria-expanded', String(open));
  });
  $('#closeDetail').addEventListener('click', closeDetail);
  document.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.space) setSpace(button.dataset.space);
    else if (button.dataset.department || button.dataset.inspect) showDepartment(button.dataset.department || button.dataset.inspect);
    else if (button.dataset.resource) showResource(button.dataset.resource);
    else if (button.dataset.detail === 'plan') showPlan();
    else if (button.dataset.detail === 'history') showHistory();
    else if (button.dataset.detail === 'gandalf') openDetail('COMPANY / SOLE LEAD', 'Gandalf', `<p class="lede">One owner conversation. Clear assignments. Evidence before completion.</p><dl><dt>Plans</dt><dd>Defines scope, acceptance and the ordered steps.</dd><dt>Coordinates</dt><dd>Assigns bounded work to functional departments. Independent work can run in parallel.</dd><dt>Resolves</dt><dd>Receives blockers and decides corrections.</dd><dt>Closes</dt><dd>Requests independent final verification and returns the result to you.</dd></dl><p class="detail-note">Fast, mechanical tasks can use the company’s Fast Path. Departments remain subordinate to Gandalf.</p>`);
    else if (button.id === 'approvePlan') {
      $('#taskStatus').textContent = 'Engineering assigned · preview';
      $('#taskMeta').textContent = 'Step 1 of 3 · verification follows implementation';
      closeDetail();
      notify('Preview only · no agent run started.');
    } else if (button.id === 'inspectResult') openDetail('WORKSPACE / RESULT', 'Wallpaper atmosphere', `<p class="lede">A completed task keeps the outcome and its evidence together.</p><dl><dt>Result</dt><dd>Landscape and constellation share a quieter middle distance.</dd><dt>Evidence</dt><dd>Changed files, comparison images and revision link.</dd><dt>Independent review</dt><dd>Acceptance findings and any coverage limits.</dd></dl><p class="detail-note">This is an example of result presentation, not a new verification claim.</p>`);
    else if (button.id === 'editMemory') {
      openDetail('MEMORY / COMPANY SCOPE', 'Owner design preferences', `<label class="sr-only" for="memoryEditor">Memory content</label><textarea id="memoryEditor" class="memory-editor"></textarea><button class="primary" id="saveMemory">Save preview entry ↗</button><p class="detail-note">Edits are for this prototype session only.</p>`);
      $('#memoryEditor').value = memoryDraft;
    }
    else if (button.id === 'saveMemory') { memoryDraft = $('#memoryEditor').value; notify('Saved for this preview session.'); closeDetail(); }
  });
  document.addEventListener('input', (event) => {
    if (event.target.id !== 'resourceSearch') return;
    const query = event.target.value.toLowerCase();
    for (const entry of document.querySelectorAll('#resourceEntries .resource-entry')) entry.hidden = !entry.textContent.toLowerCase().includes(query);
  });
  $('#composer').addEventListener('submit', (event) => {
    event.preventDefault();
    const text = $('#taskInput').value.trim();
    if (!text) return;
    const article = document.createElement('article'); article.className = 'message owner-message';
    const speaker = document.createElement('p'); speaker.className = 'speaker'; speaker.textContent = 'YOU';
    const body = document.createElement('p'); body.textContent = text;
    article.append(speaker, body); $('#conversation').append(article);
    $('#taskInput').value = ''; article.scrollIntoView({block:'nearest'});
    notify('Message shown in preview · no agent service is connected.');
  });
  const editable = (target) => target instanceof Element && Boolean(target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="searchbox"],[role="combobox"]'));
  const shortcuts = (event) => {
    if (event.isComposing || event.keyCode === 229 || editable(event.target) || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key.toLowerCase() === 'c' && !event.repeat) { event.preventDefault(); event.stopImmediatePropagation(); if (dialog.hidden) $('#cTrigger').click(); }
    if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); if (!dialog.hidden) closeDetail(); else closeMenu(); }
  };
  document.addEventListener('keydown', shortcuts, true);
  dialog.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    const items = [...dialog.querySelectorAll('button:not([disabled]),input,textarea,summary')].filter(item => !item.hidden && item.getClientRects().length);
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  frame.addEventListener('load', () => {
    frameDocument = frame.contentDocument;
    const style = frameDocument.createElement('style');
    style.textContent = `#controlsWrap{display:none!important}#clock{visibility:hidden}.clock-face{visibility:hidden}.center{transition:opacity .65s,transform .65s}.preview-away .center{opacity:0;pointer-events:none;transform:translate(-50%,12px)}.preview-detail-open #agentPanel{opacity:0;pointer-events:none}@media(max-width:820px){.preview-away #agentPanel{opacity:0;pointer-events:none}}`;
    frameDocument.head.append(style);
    frameDocument.addEventListener('keydown', shortcuts, true);
    syncFrame();
  });
  window.addEventListener('hashchange', () => setSpace(location.hash.slice(1)));
  setSpace(location.hash.slice(1) || 'home');
})();
